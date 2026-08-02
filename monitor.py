import os
import re
import hashlib
import difflib
import requests
from google import genai
from playwright.sync_api import sync_playwright, Error as PlaywrightError

# ==========================================
# 設定値の読み込み
# ==========================================
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
TARGET_URLS_RAW = os.getenv("TARGET_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ==========================================
# Google GenAI クライアントの初期化
# ==========================================
client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# オプション設定
# ==========================================
# LLM（Gemini）による要約機能を使うかどうか (True: 使う / False: 使わない)
USE_LLM_SUMMARY = True

# 監視範囲（CSSセレクタ）の設定
DEFAULT_SELECTOR = "table"
CUSTOM_SELECTORS = {
    1: "body",
}


def get_candidate_models(client):
    """
    APIから利用可能なモデル一覧を取得し、優先順位付きの候補リストを返す。
    - 優先度1: lite / flash などの軽量モデル (pro等を除外)
    - 優先度2: -001 などの廃止予定スナップショットではなく、エイリアス名を優先
    - 優先度3: 停止済み(404)を防ぐため、サポート中の最新バージョンを優先
    """
    # 通信エラーや取得失敗時の予備モデルリスト
    fallback_candidates = ["gemini-3.1-flash-lite", 
                           "gemini-2.5-flash-lite", 
                           "gemini-2.5-flash", 
                           "gemini-2.0-flash-lite", 
                           "gemini-2.0-flash", 
                           ]

    try:
        raw_models = client.models.list()
        candidates = []

        for m in raw_models:
            name = m.name.replace("models/", "")
            name_lower = name.lower()

            # テキスト生成 (generateContent) をサポートしているか確認
            methods = getattr(m, "supported_generation_methods", [])
            if methods and "generateContent" not in methods:
                continue

            if "gemini" not in name_lower:
                continue

            # pro などの非軽量モデルは完全に除外
            is_lite = "lite" in name_lower
            is_flash = "flash" in name_lower
            if not (is_lite or is_flash):
                continue

            # モデル名からバージョン番号を抽出し数値化
            v_match = re.search(r"gemini-(\d+(?:\.\d+)*)", name_lower)
            v_tuple = tuple(map(int, v_match.group(1).split("."))) if v_match else (0, 0)

            # 末尾が "-001", "-002" などのスナップショット名であるかを判定
            is_snapshot = bool(re.search(r"-\d{3,}$", name_lower))

            candidates.append({
                "name": name,
                "version": v_tuple,
                "is_lite": is_lite,
                "is_flash": is_flash,
                "is_snapshot": is_snapshot
            })

        if not candidates:
            return fallback_candidates

        # 優先順位のソート条件:
        # 1. lite 優先 (lite: 2 > flash: 1)
        # 2. スナップショットでない安定エイリアス名を優先 (True > False)
        # 3. 廃止(404)を避けるため、アクティブな最新バージョンを優先
        candidates.sort(
            key=lambda x: (
                2 if x["is_lite"] else 1,
                not x["is_snapshot"],
                x["version"]
            ),
            reverse=True
        )

        model_names = [c["name"] for c in candidates]
        return model_names if model_names else fallback_candidates

    except Exception as e:
        print(f"モデル一覧の取得中にエラーが発生しました ({e})。フォールバックリストを使用します。")
        return fallback_candidates


def send_ntfy_notification(title, message):
    """ntfy.shへプッシュ通知を送信する関数"""
    if not NTFY_TOPIC:
        print("エラー: NTFY_TOPIC が設定されていないため、通知を送信できません。")
        return

    ntfy_url = f"https://ntfy.sh/{NTFY_TOPIC}"
    try:
        response = requests.post(
            ntfy_url,
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "high",
                "Tags": "bell,warning",
            },
        )
        print(f"ntfy通知を送信しました。ステータス: {response.status_code}")
    except Exception as e:
        print(f"ntfy通知の送信に失敗しました: {e}")


def get_state_filename(url):
    """URLごとに固有の保存ファイル名を生成する"""
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
    return f"state_{url_hash}.txt"


def generate_diff_summary(old_text, new_text, max_lines=10):
    """ルールベースで差分（追加・削除行）の抜粋を作成する"""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    diff_lines = [
        line
        for line in diff
        if (line.startswith("+") or line.startswith("-"))
        and not (line.startswith("---") or line.startswith("+++"))
    ]

    if not diff_lines:
        return "（明確なテキスト差分を抽出できませんでした）"

    if len(diff_lines) > max_lines:
        summary = "\n".join(diff_lines[:max_lines])
        summary += f"\n...他 {len(diff_lines) - max_lines} 行の変更あり"
    else:
        summary = "\n".join(diff_lines)

    return summary


def get_llm_summary(old_text, new_text):
    """
    動的に候補モデル一覧を取得し、成功するまで順番にリトライ生成を行う
    """
    if not client:
        return "（エラー: GEMINI_API_KEY が設定されていません）"

    # 優先度順のモデル候補リストを取得
    candidate_models = get_candidate_models(client)

    prompt = f"""
        あなたはウェブサイトの監視アシスタントです。
        以下の「古いテキスト」から「新しいテキスト」へ変更がありました。
        どのような情報が更新されたのか、ユーザーがスマホの通知で一目でわかるように、簡潔な日本語で要約してください。
        挨拶や余計な説明は不要です。変更の要点のみを2〜3行程度で出力してください。
        
        【古いテキスト】
        {old_text[:1000]}
        
        【新しいテキスト】
        {new_text[:1000]}
        """

    # 候補リストを順番に試す（万が一 404 やエラーが出ても次のモデルへ自動フォールバック）
    for model_name in candidate_models:
        try:
            print(f"【AI要約試行】モデル '{model_name}' で要約を呼び出し中...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            summary = response.text.strip()
            print(f"【成功】Gemini API ({model_name}) での要約生成に成功しました。")
            return summary

        except Exception as e:
            print(f"【警告】Gemini API エラー ({model_name}): {e}")
            print("次の軽量モデル候補を試します...")

    return "（AI要約の取得に失敗しました）"


def process_url(page, url, selector):
    """単一のURLに対する監視処理"""
    url = url.strip()
    if not url or not url.startswith("http"):
        print(f"スキップ: 無効なURL形式です ({url})")
        return

    state_file = get_state_filename(url)

    try:
        print(f"\n--- アクセス中: {url} ---")
        print(f"監視セレクタ: {selector}")

        page.goto(url, wait_until="networkidle", timeout=30000)

        if selector:
            texts = page.locator(selector).all_inner_texts()
            current_text = "\n".join(texts)
            if not current_text.strip():
                print(f"警告: 指定したセレクタ '{selector}' に該当するテキストが見つかりませんでした。")
        else:
            current_text = page.locator("body").inner_text()

        current_text = current_text.strip()
        last_text = ""

        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                last_text = f.read().strip()

        if last_text != "" and current_text != last_text:
            print(f"【検知】更新を確認: {url}")

            # 1. ルールベースの差分抜粋
            diff_summary = generate_diff_summary(last_text, current_text)

            # 2. LLMによる要約 (フラグがTrueの場合のみ実行)
            llm_summary = ""
            if USE_LLM_SUMMARY:
                print("LLMで要約を生成中...")
                llm_summary = f"\n\n【AI要約】\n{get_llm_summary(last_text, current_text)}"

                # 通知メッセージの組み立て
                notification_message = (
                    f"【対象URL】\n{url}\n\n"
                    f"{llm_summary}"
                )
            else:
                # 通知メッセージの組み立て
                notification_message = (
                    f"【対象URL】\n{url}\n\n"
                    f"【変更の抜粋 (-削除 / +追加)】\n{diff_summary}"
                )

            send_ntfy_notification(
                title="【更新検知】予約状況・内容が変わりました！",
                message=notification_message,
            )

            with open(state_file, "w", encoding="utf-8") as f:
                f.write(current_text)

        elif last_text == "":
            print("初回実行のため、現在の状態を記録します。")
            with open(state_file, "w", encoding="utf-8") as f:
                f.write(current_text)

        else:
            print("変更はありませんでした。")

    except PlaywrightError as e:
        error_msg = f"アクセス失敗 ({url})\n詳細: {e}"
        print(f"【エラー】{error_msg}")
        send_ntfy_notification("【監視エラー】アクセス失敗", error_msg)

    except Exception as e:
        error_msg = f"予期せぬエラー ({url})\n詳細: {e}"
        print(f"【エラー】{error_msg}")
        send_ntfy_notification("【監視エラー】システムエラー", error_msg)


def main():
    if not NTFY_TOPIC:
        print("環境変数 'NTFY_TOPIC' が設定されていません。")
        return

    if not TARGET_URLS_RAW:
        error_msg = "環境変数 'TARGET_URL' が設定されていません。"
        print(error_msg)
        send_ntfy_notification("【設定エラー】", error_msg)
        return

    url_list = [u.strip() for u in TARGET_URLS_RAW.splitlines() if u.strip()]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            for index, url in enumerate(url_list):
                selector = CUSTOM_SELECTORS.get(index, DEFAULT_SELECTOR)
                process_url(page, url, selector)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
    
