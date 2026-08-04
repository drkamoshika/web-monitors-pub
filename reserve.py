import os
import json
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

# ------------------------------------------------------------------
# 環境変数からURLを取得
# ------------------------------------------------------------------
def get_target_url():
    url = os.environ.get('RESERVE_URL') or os.environ.get('TARGET_URL', '')
    return url.strip().splitlines()[0] if url.strip() else ""


def send_ntfy(message, image_path=None):
    """ntfyへメッセージと画像を送信する関数"""
    ntfy_topic = os.environ.get('NTFY_TOPIC', 'my_secret_reserve_2026')
    url = f"https://ntfy.sh/{ntfy_topic}"
    
    try:
        if image_path and os.path.exists(image_path):
            filename = os.path.basename(image_path)
            params = {
                "file": filename,
                "title": "自動予約レポート",
                "message": message
            }
            with open(image_path, 'rb') as f:
                requests.put(url, data=f, params=params, timeout=10)
        else:
            requests.post(
                url,
                data=message.encode('utf-8'),
                headers={"Title": "自動予約レポート"},
                timeout=10
            )
    except Exception as e:
        print(f"ntfy送信失敗: {e}")


def robust_click(page, selectors, description="要素"):
    """複数セレクターを順番に試行してクリック（高速化のためタイムアウト500ms）"""
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=500):
                loc.click()
                print(f"成功: {description} ({selector})")
                return True
        except Exception:
            continue
    return False


def check_and_run_reserve(target_days, execute_submit=False):
    """
    指定された日付リスト(target_days)をチェックし、
    空きがあれば1回のブラウザ起動でそのまま予約完了まで実行する
    """
    if not target_days:
        print("TARGET_DAYS が空のため、自動予約チェックをスキップします。")
        return

    reserve_url = get_target_url()
    if not reserve_url:
        print("予約URLが設定されていないため、自動予約チェックをスキップします。")
        return

    print("\n--- 自動予約チェック＆実行開始 ---")

    # 1. PAYLOAD の安全なデコード
    payload_raw = os.environ.get('PAYLOAD', '{}')
    try:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    except Exception:
        payload = {}

    if isinstance(payload, dict) and 'client_payload' in payload:
        payload = payload['client_payload']

    if isinstance(payload, dict) and 'data_json' in payload:
        try:
            data = json.loads(payload['data_json']) if isinstance(payload['data_json'], str) else payload['data_json']
        except Exception:
            data = payload
    else:
        data = payload if isinstance(payload, dict) else {}

    # 2. 単一のブラウザセッションで判定から予約までを一括実行
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 1000},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()

        try:
            # [画面1] カレンダー画面読み込み
            page.goto(reserve_url, wait_until='domcontentloaded')

            found_date = None
            found_yymmdd = None

            # 対象日の空き枠判定
            for target_date in target_days:
                dt = datetime.strptime(target_date, '%Y-%m-%d')
                yymmdd = dt.strftime('%y%m%d')

                link = page.locator(f'a[href*="{yymmdd}"]')
                if link.count() > 0:
                    print(f"【朗報】{target_date} の空き枠を検知しました！")
                    found_date = target_date
                    found_yymmdd = yymmdd
                    break
                else:
                    print(f"{target_date} の空き枠はありませんでした。")

            if not found_date:
                print("該当する空き枠が見つからなかったため、予約処理を終了します。")
                return

            # --- 空き枠が見つかったため、そのまま同一ページで予約を進める ---
            dt = datetime.strptime(found_date, '%Y-%m-%d')
            date_label = dt.strftime('%Y年%m月%d日')

            img1 = f"01_calendar_{found_yymmdd}.png"
            page.screenshot(path=img1, full_page=True)
            send_ntfy(f"【自動予約開始】{date_label} 選択直前", img1)

            date_selectors = [
                f'a[href*="{found_yymmdd}"]',
                f'a:has-text("{dt.day}日")',
                f'td:has-text("{dt.day}") a'
            ]
            if not robust_click(page, date_selectors, "日付リンク"):
                raise Exception(f"{date_label} ({found_yymmdd}) の予約リンクが見つかりません。")

            # [画面2] プラン選択画面
            page.wait_for_load_state('domcontentloaded')

            img2 = f"02_plan_{found_yymmdd}.png"
            page.screenshot(path=img2, full_page=True)
            send_ntfy(f"【画面2: プラン選択】{date_label} ご予約ボタン押下直前", img2)

            reserve_button_selectors = [
                'a[href*="f2.asp"]',
                'tr:has-text("一泊二食") a',
                'a:has-text("ご予約")'
            ]

            if not robust_click(page, reserve_button_selectors, "ご予約はこちらボタン"):
                page.evaluate('''() => {
                    const link = Array.from(document.querySelectorAll('a')).find(a => a.href.includes('f2.asp') || a.innerText.includes('予約'));
                    if (link) link.click();
                    else throw new Error("予約リンク(f2.asp)が存在しません");
                }''')

            # [画面3] 予約フォーム入力画面
            page.wait_for_load_state('domcontentloaded')

            def fill_field(label_text, value, is_textarea=False):
                if not value: return
                elem_type = "textarea" if is_textarea else "input"
                selectors = [
                    f'tr:has-text("{label_text}") {elem_type}',
                    f'td:has-text("{label_text}") ~ td {elem_type}'
                ]
                for sel in selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=200): # 高速化のため待ち時間を200msに短縮
                            loc.fill(str(value))
                            return
                    except Exception:
                        continue

            def select_field(label_text, value, index=0):
                selectors = [
                    f'tr:has-text("{label_text}") select',
                    f'td:has-text("{label_text}") ~ td select'
                ]
                for sel in selectors:
                    try:
                        locs = page.locator(sel)
                        if locs.count() > index:
                            locs.nth(index).select_option(str(value))
                            return
                    except Exception:
                        continue

            # 各項目の自動入力
            select_field("宿泊人数", data.get('total_guests', '1'))
            select_field("男女内訳", data.get('male_guests', '1'), index=0)
            select_field("男女内訳", data.get('female_guests', '0'), index=1)

            if str(data.get('youth_discount', '0')) != '0':
                select_field("ユース割引", data.get('youth_discount', '0'))
                select_field("19-30", data.get('youth_discount', '0'))

            if str(data.get('bento_count', '0')) != '0':
                select_field("弁当の追加", data.get('bento_count', '0'))
                select_field("昼弁当", data.get('bento_count', '0'))

            fill_field("お名前", data.get('name', ''))
            fill_field("ふりがな", data.get('furigana', ''))

            email = data.get('email', '')
            email_locs = page.locator('tr:has-text("メールアドレス") input, input[type="email"]')
            if email_locs.count() >= 2:
                email_locs.nth(0).fill(email)
                email_locs.nth(1).fill(email)
            elif email_locs.count() >= 1:
                email_locs.first.fill(email)

            fill_field("郵便番号", data.get('zip', ''))

            pref_loc = page.locator('tr:has-text("ご住所") select').first
            try:
                if pref_loc.is_visible(timeout=200):
                    pref_loc.select_option(label=data.get('pref', '東京都'))
            except Exception:
                pass

            fill_field("ご住所", data.get('address', ''))
            fill_field("連絡先電話番号", data.get('phone', ''))

            prev_stay = data.get('prev_stay') or data.get('entry_point') or '新穂高口'
            next_stay = data.get('next_stay') or data.get('exit_point') or prev_stay

            stay_row = page.locator('tr:has-text("前・後泊地"), tr:has-text("入山口")').first
            stay_inputs = stay_row.locator('input[type="text"], input:not([type="hidden"]):not([type="checkbox"])')

            if stay_inputs.count() >= 2:
                stay_inputs.nth(0).fill(str(prev_stay))
                stay_inputs.nth(1).fill(str(next_stay))
            else:
                page.evaluate('''({prev, next}) => {
                    const trs = Array.from(document.querySelectorAll('tr'));
                    const targetTr = trs.find(tr => tr.innerText.includes('前') && tr.innerText.includes('後'))
                                  || trs.find(tr => tr.innerText.includes('入山口'));
                    if (targetTr) {
                        const inputs = Array.from(targetTr.querySelectorAll('input[type="text"], input:not([type="hidden"])'));
                        if (inputs.length >= 1) {
                            inputs[0].value = prev;
                            inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
                            inputs[0].dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        if (inputs.length >= 2) {
                            inputs[1].value = next;
                            inputs[1].dispatchEvent(new Event('input', { bubbles: true }));
                            inputs[1].dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }
                }''', {'prev': prev_stay, 'next': next_stay})

            fill_field("自由記入", data.get('memo', ''), is_textarea=True)

            cb = page.locator('input[type="checkbox"]').first
            if cb.is_visible(timeout=200):
                cb.check()

            img3 = f"03_form_{found_yymmdd}.png"
            page.screenshot(path=img3, full_page=True)
            send_ntfy(f"【画面3: フォーム入力】{date_label} 「次へ」押下直前", img3)

            next_button_selectors = ['input[value*="次"]', 'button:has-text("次")', 'input[type="submit"]']
            robust_click(page, next_button_selectors, "次へボタン")

            # [画面4] 確認画面
            page.wait_for_load_state('domcontentloaded')

            has_error = (
                page.get_by_text("誤りがあります").count() > 0 or 
                page.get_by_text("必ず、入力してください").count() > 0
            )
            is_confirm_page = page.locator('td:has-text("2.予約内容の確認"), div:has-text("2.予約内容の確認")').count() > 0

            if has_error or not is_confirm_page:
                err_img = f"error_validation_{found_yymmdd}.png"
                page.screenshot(path=err_img, full_page=True)
                raise Exception("フォームの必須入力欄にエラーがあるため、確認画面に進めませんでした。")

            img4 = f"04_confirm_{found_yymmdd}.png"
            page.screenshot(path=img4, full_page=True)

            if not execute_submit:
                send_ntfy(f"【確認画面到達】{date_label} （※自動送信オフモードのため送信ボタンは押さずに終了します）", img4)
                print("execute_submit が False のため、最終送信を行わずに完了しました。")
                return

            send_ntfy(f"【画面4: 確認画面】{date_label} 最終送信ボタン押下直前", img4)

            # [画面5] 最終送信実行
            final_submit_selectors = [
                'input[value*="送"]', 'input[value*="確"]', 'input[value*="申"]',
                'button:has-text("送信")', 'button:has-text("確定")', 'button:has-text("申込")',
                'a:has-text("送信")', 'a:has-text("確定")', 'input[type="submit"]'
            ]

            if not robust_click(page, final_submit_selectors, "最終送信ボタン"):
                page.evaluate('''() => {
                    const elements = Array.from(document.querySelectorAll('input, button, a'));
                    const target = elements.find(el => {
                        const txt = (el.value || el.innerText || '').replace(/\\s+/g, '');
                        return txt.includes('送信') || txt.includes('確定') || txt.includes('予約申込') || txt.includes('申し込む');
                    });
                    if (target) target.click();
                    else {
                        const form = document.querySelector('form');
                        if (form) form.submit();
                        else throw new Error("送信ボタンが見つかりませんでした");
                    }
                }''')

            page.wait_for_load_state('domcontentloaded')

            img5 = f"05_completion_{found_yymmdd}.png"
            page.screenshot(path=img5, full_page=True)
            send_ntfy(f"【予約完全完了】{date_label} 予約送信が完了しました！", img5)

        except Exception as e:
            err_img = f"error_run.png"
            page.screenshot(path=err_img, full_page=True)
            send_ntfy(f"【エラー発生】処理中断: {str(e)}", err_img)

        finally:
            browser.close()


def run_reserve(target_date, execute_submit=False, custom_data=None):
    """直接日付指定で予約を実行したい場合の互換用関数"""
    check_and_run_reserve([target_date], execute_submit=execute_submit)


if __name__ == "__main__":
    pass

