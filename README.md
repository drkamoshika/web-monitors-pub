# web-monitors-pub
- 複数web監視＋通知ツール。
- 監視したいwebサイトはsecretsに改行区切りで登録すること。

## 定期実行について
- GitHub actions（＝.github/workflowsのyamlにscheduleを書いて定期実行）は安定しないので、cron-job.orgに定期実行をさせる。
  - この運用だとpublicにいなくてもいいのでprivateに戻すこと



この方法を使えば、現在作っている Python スクリプト（Playwright や ntfy の仕組み）や `monitor.yml` を一切壊すことなく、**「30分おきに確実に GitHub Actions に合図を出して実行させる」** 構成が完成します。

設定は **10分ほど** で完了します。手順を4つのステップに分けて分かりやすく解説します。

---

## 全体イメージ

```text
[ cron-job.org ] ──(30分ごとにAPI送信)──> [ GitHub Actions ] ──> [ monitor.py 実行 ]

```

`cron-job.org` が合図（Web API）を送り、GitHub Actions の `workflow_dispatch`（手動実行ボタンを押すのと同じ処理）を自動で起動させます。

---

## STEP 1: GitHubで「合図を受け取るための鍵（トークン）」を発行する

`cron-job.org` があなたのGitHubに代わりに指示を出せるよう、専用のアクセス鍵（Personal Access Token）を発行します。

1. GitHub画面の右上にある **自分のプロフィールアイコン** をクリックし、**「Settings」** を開きます。
2. 左メニューの最下部にある **「Developer settings」** をクリックします。
3. **Personal access tokens** $\rightarrow$ **Tokens (classic)** をクリックします。
4. 右上の **「Generate new token」** $\rightarrow$ **「Generate new token (classic)」** をクリックします。
5. 設定画面で以下のように入力します：
* **Note:** `cron-job-trigger`（分かりやすい名前でOK）
* **Expiration:** `No expiration`（無期限）、または任意の期限
* **Select scopes:** **`workflow`** にチェックを入れる（これで自動的に `repo` など必要な権限が付きます）


6. ページ一番下の **「Generate token」** ボタンをクリックします。
7. 画面に **`ghp_` から始まる英数字の長い文字列** が表示されます。
> ⚠️ **注意:** この画面を閉じると二度と表示されません。必ずメモ帳などにコピーしておいてください。



---

## STEP 2: cron-job.org に無料登録する

1. [cron-job.org](https://cron-job.org/) の公式サイトにアクセスします。
2. **「Sign Up」** からアカウントを作成します（メールアドレスとパスワードのみでOK）。
3. 届いた確認メールのリンクをクリックしてアカウントを有効化し、ログインします。

---

## STEP 3: cron-job.org で定期実行ジョブを作成する

ログイン後、ダッシュボードから新しいジョブを作成します。

1. 上部メニューの **「Cronjobs」** を開き、右上の **「Create cronjob」** ボタンをクリックします。
2. 各項目を以下のように設定します：
#### 【基本設定 (Common)】


* **Title:** `Web Monitor Trigger`（任意の名前）
* **URL:** 以下のように自分のユーザー名とリポジトリ名に書き換えて入力します。
```text
https://api.github.com/repos/【GitHubのユーザー名】/【リポジトリ名】/actions/workflows/monitor.yml/dispatches

```


*(例: `[https://api.github.com/repos/yamada/web-monitor/actions/workflows/monitor.yml/dispatches](https://api.github.com/repos/yamada/web-monitor/actions/workflows/monitor.yml/dispatches)`)*


#### 【スケジュール設定 (Schedule)】


* **Execution schedule:** `Every 30 minutes`（30分ごと）を選択


#### 【詳細設定 (Advanced)】※ここが一番重要です！


* **Request method:** **`POST`** を選択
* **Headers (ヘッダーの追加):** 「Add header」をクリックして、以下の **4つ** を登録します。
| Header Key | Value |
| --- | --- |
| `Authorization` | `Bearer ghp_xxxx...` *(STEP 1で取得した鍵。Bearerとghpの間に半角スペース)* |
| `Accept` | `application/vnd.github+json` |
| `User-Agent` | `cron-job-org` |
| `X-GitHub-Api-Version` | `2022-11-28` |


* **Request body (リクエスト本文):**
* 「Send raw data」にチェックを入れます。
* 本文に以下を入力します（デフォルトブランチが `main` の場合）：
```json
{"ref": "main"}

```


*(※もしブランチ名が `master` の場合は `"master"` にしてください)*




3. 入力できたら、一番下の **「Create」** をクリックして保存します。

---

## STEP 4: テスト実行と動作確認

作成したジョブが正しく動くかテストしてみましょう！

1. `cron-job.org` のジョブ一覧画面で、今作成したジョブの右側にある **「Test（または再生ボタンのようなマーク）」** をクリックします。
2. ステータスが **`204 No Content`** または **`200 OK`** と表示されれば成功です！
3. すぐに GitHub のリポジトリ画面を開き、**「Actions」** タブを確認してください。
4. 手動で動かした時と同じように、ワークフローが自動でグルグルと回り始めていれば **完璧に連携できています！**

---

## 💡 運用上のワンポイントアドバイス

### 1. `monitor.yml` から不要な `schedule` を外す（推奨）

今後定期実行は `cron-job.org` がすべて仕切ってくれるため、GitHub側の気まぐれな `schedule` は消しておいたほうが無駄な二重実行を防げます。

`.github/workflows/monitor.yml` の `on:` 部分を以下のようにスッキリさせておきましょう。

```yaml
name: Web Monitoring Workflow

on:
  workflow_dispatch: # cron-job.org や手動からの呼び出しのみ受け付ける

permissions:
  contents: write

jobs:
  # (以下変更なし)

```

### 2. 万が一エラー（403や404）が出た場合

* **404 Not Found:** `URL` の「ユーザー名」「リポジトリ名」「ワークフローファイル名（`monitor.yml`）」のいずれかが間違っている可能性があります。
* **403 Forbidden:** STEP 1 で作成したトークン（`ghp_...`）の権限不足か、`User-Agent` などのヘッダーの入力ミスです。

これで、遅延に悩まされることなく **30分おきにピッタリ正確に動作するWeb監視システム** の運用スタートです！
