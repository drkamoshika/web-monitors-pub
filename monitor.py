import os
import hashlib
import requests
from playwright.sync_api import sync_playwright, Error as PlaywrightError

# ==========================================
# 設定値の読み込み
# ==========================================
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
TARGET_URLS_RAW = os.getenv("TARGET_URL")

# ==========================================
# 監視範囲（CSSセレクタ）の設定
# ==========================================
# デフォルトのセレクタ（基本はこれを使います）
DEFAULT_SELECTOR = "table"

# URLの順番（0から始まる番号）に合わせて、個別のセレクタを指定します
# 例: 0番目はそのままtable、1番目（2つ目のURL）はbodyにする場合
CUSTOM_SELECTORS = {
    1: "body",
}


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
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    return f"state_{url_hash}.txt"

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
        
        # アクセス処理（タイムアウト30秒）
        page.goto(url, wait_until="networkidle", timeout=30000)

        # セレクタに基づいてテキストを取得
        if selector:
            texts = page.locator(selector).all_inner_texts()
            current_text = "\n".join(texts)
            if not current_text.strip():
                print(f"警告: 指定したセレクタ '{selector}' に該当するテキストが見つかりませんでした。")
        else:
            current_text = page.locator("body").inner_text()

        current_text = current_text.strip()

        # 前回保存したテキスト状態の読み込み
        last_text = ""
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                last_text = f.read().strip()

        # 比較・判定と保存
        if last_text != "" and current_text != last_text:
            print(f"【検知】更新を確認: {url}")
            send_ntfy_notification(
                title="【更新検知】予約状況が変わりました！",
                message=f"カレンダー等の内容に変更がありました。\n{url}"
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

    # Secretsから改行区切りで取得し、空行を省いてリスト化する
    url_list = [u.strip() for u in TARGET_URLS_RAW.splitlines() if u.strip()]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            for index, url in enumerate(url_list):
                # カスタムセレクタが設定されていればそれを取得し、無ければデフォルトを使う
                selector = CUSTOM_SELECTORS.get(index, DEFAULT_SELECTOR)
                process_url(page, url, selector)
        finally:
            browser.close()

if __name__ == "__main__":
    main()
    
