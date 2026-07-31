# Claude Code — Ranbell Image

画像制作は **Muse**（旧 Chronicle / Weave はどちらも撤去済み）。
お題とキャラから安いイメージボードを描き、その絵から WD14 でタグを拾い直して本番を描く。

設計: [docs/guide/muse.ja.md](docs/guide/muse.ja.md)

意外性の仕組みは `backend/app/invoke/vocab_bank.py`、
タグ結合は `backend/app/prompt/tag_merge.py` にある。どちらも再発明しないこと。

gitignore された運用メモがある場合: **[private/CLAUDE.md](private/CLAUDE.md)**
