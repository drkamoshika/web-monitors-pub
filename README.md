# web-monitors-pub
- 複数web監視＋通知ツール。
- 監視したいwebサイトはsecretsに改行区切りで登録すること。

## 定期実行について
- GitHub actions（＝.github/workflowsのyamlにscheduleを書いて定期実行）は安定しないので、cron-job.orgに定期実行をさせる。
  - この運用だとpublicにいなくてもいいのでprivateに戻すこと
