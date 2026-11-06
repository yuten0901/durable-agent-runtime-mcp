# Status

- 状態: 試作中
- 結論日: 2026-11-06

## 結論

外部公開、課金、Client連絡、認証、外部APIを使わず、5役のsubagent依存実行、SQLite
checkpoint、制御例外後に新しいruntime/store objectから再開する試験、Apify相当adapterのbounded retry、JSON-only sandbox、
stale lease回収、構造化trace、評価runnerを備えた汎用AI agent runtime demoを実装した。

2026-11-06に、外部SDK・外部通信・資格情報を使わないMCP JSON-RPC stdio serverを追加した。
許可済みfixtureの列挙、workflow開始、checkpoint再開、状態・trace取得をtoolとして公開し、
tool callerが任意file pathを渡せないcatalog境界と、canonical snapshot・SHA-256で内容を固定した
fixture identityからの再開を実装した。中断後に同じfixture IDの内容が変わればfail-closedで拒否する。

評価は正常、一時actor障害、sandbox path escapeの3/3シナリオが期待どおりとなり、
独立レビューのHigh 1件・Medium 3件に加え、Round 2のMedium 2件を修正した。公開手順の
initialize例は必須paramsとinitialized notificationを含み、文書transcript自体を回帰試験する。
また、`id`なしobjectはJSON-RPC共通fieldの形状を先に検証し、malformed objectだけを`-32600`
（`id: null`）で返し、正規notificationは無応答とする境界を実装した。自動テスト47件、Ruff、
strict Mypy、compileallは成功し、machine-readable evaluationはruntime fixture 3/3とMCP境界検査
4/4を記録する。Round 3の独立再レビューは`ACCEPTED（新規findingなし）`で、公開JSONLの
実stdio実行、malformed object 4/4、正規notification 3/3も再確認した。

公開直前監査では、Client価値が冒頭で伝わるREADME、外部依存のないruntime図、checkpoint後の
制御例外からのobject再構築を1 commandで示す短いdemo、公開対象と内部レビュー原本の境界を追加した。LICENSEはMIT、
秘密情報は検出されず、公開予定fileだけのclean-tree相当検証を行う。Git repositoryは初回commit前で
全fileがuntrackedのため、commit禁止の今回作業では公開可能なtracked状態への変更は行わない。
clean-tree相当環境では短いdemo、47 test、compileall、workflow評価3/3、MCP評価4/4が成功した。

独立公開レビュー後、実証境界をさらに明確化した。中断試験はOS subprocessを終了せず、同一Python
process内で例外を発生させ、最初のstoreを閉じて新しいruntime/store objectを構築するものとREADME、
図、設計・評価文書へ明記した。実process終了後の別process再開は未実証である。またbuild backendを
backend固有機能に依存しないsetuptoolsへ変更した。公開予定fileだけの一時treeから外部通信なしで
wheelをbuildし、fresh Python 3.14 venvへinstallした。repository外のcwdから同梱fixtureによるdemoが
成功し、wheel内のJSON fixture、LICENSE、metadataも確認した。

正式な公開file集合を`release-manifest.txt`へ固定した。検証scriptはmanifest対象だけから一時Git
repositoryとcommitを作り、local cloneでcontent gate、47 test、Ruff、strict Mypy、compileall、
workflow/MCP評価、offline wheel、fresh venv demoを実行する。元repositoryのindex・branch・commitは
変更しない。第一印象用PNGと編集可能なSVG sourceもmanifestへ含め、署名、1000×750寸法、chunk完結性、metadata非混入を
機械検査する。commit単位、内部report除外、remote rollbackはpublication runbookへ記録した。

実証範囲はローカルfixtureでのruntime制御と障害時挙動である。実Apify、外部LLM、任意code
のOS隔離、Next.js、Cloudflare Workers/D1、外部deployment、長時間soak、本番運用は未検証。
公開可否は別途判断するため、フォルダは`wip-`のままとする。

## 再利用できる資産

- dependency-awareなsubagent planと永続checkpoint
- idempotency key付きactor adapter境界
- retry上限、terminal failure、stale lease回収
- path containmentと拡張子allowlistを持つartifact sandbox
- append-only trace、health集計、3種の評価fixture
- D1、Workers、Next.js、Apifyへ進む前の検証境界表
- MCP initialization、tool discovery、5 tools、stdio transport
- MCP経由の中断・再開と任意fixture path拒否の回帰test
- fixture snapshot/digest束縛、全tool schema強制、handshake・JSON-RPC負方向test
- `agent-runtime-demo demo`によるcheckpoint後の制御例外・新runtime object再開の短い実演
- 外部assetに依存しない公開用runtime flow SVG

## 教訓

- multi-agentという名称より、各handoffの依存条件と再実行規則の方が重要である。
- checkpointは処理成功後、外部side effectを再発させない位置で確定する必要がある。
- filesystem allowlistは任意code sandboxの証拠ではないため区別して記載する。
- SQLite schemaが単純でもD1互換性や分散claimを証明したことにはならない。
- 最小MCP stdio実装のtest成功は、全MCP clientやremote transportとの互換性を証明しない。
- protocol version 2025-06-18以外の実Client handshakeは未検証で、未知versionには対応versionを返すだけである。

## 公開前に残る外部操作

- `git ls-files --others --exclude-standard`で列挙される公開候補を人間が最終確認し、初回commitする。
- repository名・remote・公開範囲を決め、必要なら`wip-`から`prod-`へ変更して`prod` tagを付ける。
- remoteへpush後、実際のgit clean cloneからREADMEどおりのeditable installを最終確認する。
- 公開URLをREADMEのproject metadataへ追加する。

これらはcommit、push、公開、外部通信を禁止した今回の作業範囲では実行していない。
