# Decisions

## 2026-11-06: 公開直前パッケージの第一印象

- Clientが最初に知りたい「何の失敗を防ぐ証拠か」をREADME冒頭へ置く。
- hosted demoや動画ではなく、無料・offline・決定的な`agent-runtime-demo demo`を最短導線にする。
  これはcheckpoint後の制御例外と新runtime/store objectからの再開を同一process内で行い、完了済みstepのattemptが
  1のままであることを短いJSONで示す。demo fixtureはpackageへ同梱し、repositoryのcurrent
  directoryに依存させない。
- 第一印象用の図は外部assetや生成サービスへ依存しないrepository内SVGとする。
- 独立レビュー原本は監査証跡としてローカルに保持するが、Client向け公開物ではないため
  `.gitignore`で公開対象から除く。再現可能な公開証拠はtest、`reports/evaluation.json`、設計文書へ
  集約する。
- 初回commit前で全ファイルがuntrackedの状態は、commit禁止という今回の制約内では変更しない。
  公開時に明示的なfile review、初回commit、remote確認、pushが必要である。

## 2026-11-06: 証拠境界を維持する

- SVGやREADMEを追加しても、実MCP Client、remote transport、外部actor、外部LLM、D1互換性、
  本番運用を実証したとは表現しない。
- 公開前検証は外部通信を行わず、公開予定fileだけを一時directoryへ複製したclean-tree相当環境で
  sourceからdemo、test、evaluationを再実行する。
- package設定はHatch固有機能を使用していなかったため、ローカルに利用可能なsetuptoolsへbuild
  backendを変更する。`src` layoutのpackage discoveryとJSON fixture同梱を明示し、外部通信なしの
  wheel build、fresh venv install、repository外cwdからのdemoで配布境界を確認した。

## 2026-11-06: 中断試験の主張境界

- `SimulatedCrash`はOS processを終了させない。同一Python process内でcheckpoint直後に制御例外を
  発生させ、storeを閉じ、新しいruntime/store objectから再開する試験である。
- これは永続checkpointとidempotent step identityの証拠だが、実process death、別processからの
  DB再open、signal/kill途中のflush挙動を直接実証しない。Client向け価値は保ちつつ、この差をREADME、
  SVG、architecture、evaluation、deployment boundaryへ明記する。

## 2026-11-06: 公開manifestとlocal clone gate

- `.gitignore`や作業treeの偶然の状態ではなく、`release-manifest.txt`を唯一の公開file集合とする。
- verifierは元repositoryへcommitせず、manifest fileだけを一時repositoryへ複製して一時commitし、
  そのlocal cloneから全gateを実行する。これによりuntrackedの現状でもtracked publication境界を再現する。
- 現在は全成果が相互依存する初回releaseなので、正式commitは1 atomic commitを推奨する。remote rollbackは
  force-pushではなく`git revert`を標準とし、tag移動やrepository非公開化は別承認とする。
