# web-monitors-pub
- 複数web監視＋通知ツール。
- 監視したいwebサイトはsecretsに改行区切りで登録すること。
- 監視後、指定した日に予約実行もする関数を追加。

## 定期実行について
- GitHub actions（＝.github/workflowsのyamlにscheduleを書いて定期実行）は安定しないので、cron-job.orgに定期実行をさせている。
- 定期実行の有無ではなく総実行時間で課金されるのでpublicのままの方がいい

## LLM SDK
- googleのsdkを使用：https://ai.google.dev/gemini-api/docs/migrate?hl=ja
- openai互換にしてもいい：https://ai.google.dev/gemini-api/docs/openai?hl=ja
- 

## Gemini APIの料金一覧
- https://ai.google.dev/gemini-api/docs/pricing?hl=ja

## 予約処理について(注意：特注品)
- 「リストの先頭に書いた日付から順番にチェックし、どれか1つでも予約できたら（あるいは予約処理に入ったら）そこで即座に終了する」
