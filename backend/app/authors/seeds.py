"""Seed author archetypes (no personal names)."""
from __future__ import annotations

AUTHOR_SEEDS: list[dict[str, str]] = [
    # Genre
    {
        "name": "恋愛小説・余韻派",
        "genre_tag": "恋愛小説",
        "style_description": (
            "情景描写と間を多めに。感情語は抑え、仕草と光の変化で関係性を示す短文。"
        ),
    },
    {
        "name": "恋愛小説・高揚派",
        "genre_tag": "恋愛小説",
        "style_description": (
            "鼓動の速さが出る短い文。視線と距離感の変化を強調し、余韻は最小限。"
        ),
    },
    {
        "name": "ラノベ・テンポ重視",
        "genre_tag": "ラノベ",
        "style_description": (
            "テンポの速いリズム。状況説明は最小にし、動作とセリフ感のある地の文。"
        ),
    },
    {
        "name": "ラノベ・日常ほのぼの",
        "genre_tag": "ラノベ",
        "style_description": (
            "やわらかい語彙で小さな幸福と照れを余韻として残す。大げさなドラマは避ける。"
        ),
    },
    {
        "name": "ミステリー・抑制語り",
        "genre_tag": "ミステリー",
        "style_description": (
            "乾いた観察眼。事実と物証を淡々と並べ、結論は急がない。"
        ),
    },
    {
        "name": "ミステリー・不穏余白",
        "genre_tag": "ミステリー",
        "style_description": (
            "欠けた情報を暗示する。音・影・不在を先に書き、説明で埋めない。"
        ),
    },
    {
        "name": "ギャグ・テンポ破綻",
        "genre_tag": "ギャグ",
        "style_description": (
            "短く言い切り、落差と繰り返しで笑いを作る。大げさな比喩は避け動作で落とす。"
        ),
    },
    {
        "name": "ギャグ・ツッコミ地の文",
        "genre_tag": "ギャグ",
        "style_description": (
            "冷静な地の文が状況の異常さを相対化する。感情の爆発は最小限。"
        ),
    },
    {
        "name": "青春・季節感",
        "genre_tag": "青年誌/青春",
        "style_description": (
            "季節の手触りと通学路の距離感。大きな事件より空気の変化を書く。"
        ),
    },
    {
        "name": "ホラー・静かな侵食",
        "genre_tag": "ホラー",
        "style_description": (
            "日常のまま少しずつズレる。大声の恐怖語は禁止し、物の配置で不安を出す。"
        ),
    },
    {
        "name": "SF・簡潔観測",
        "genre_tag": "SF",
        "style_description": (
            "世界設定は道具一つで示す。感情より観測ログ調の簡潔さ。"
        ),
    },
    {
        "name": "叙情・詩的短文",
        "genre_tag": "文芸",
        "style_description": (
            "比喩は少なく、名詞と光の語で余白を残す。説明調を避ける。"
        ),
    },
    # Personality
    {
        "name": "寡黙で観察眼が鋭い",
        "genre_tag": "パーソナリティ",
        "style_description": (
            "一文を短く。主語を省きがち。目に入った物だけを書く。"
        ),
    },
    {
        "name": "熱量高め・言い切り",
        "genre_tag": "パーソナリティ",
        "style_description": (
            "断定調。ためらいの語尾を使わない。動作を先に出す。"
        ),
    },
    {
        "name": "自虐気味の内省",
        "genre_tag": "パーソナリティ",
        "style_description": (
            "自分の失敗や間の悪さを淡々と認めるが、感傷語は使わない。"
        ),
    },
    {
        "name": "おっとり・間が長い",
        "genre_tag": "パーソナリティ",
        "style_description": (
            "文の間を取り、環境音や光の変化を一拍置く。"
        ),
    },
    {
        "name": "皮肉屋の距離感",
        "genre_tag": "パーソナリティ",
        "style_description": (
            "感情に寄り添わず、状況を少し引いた視点で切り取る。"
        ),
    },
    {
        "name": "純度の高い真剣さ",
        "genre_tag": "パーソナリティ",
        "style_description": (
            "冗談を挟まない。視線と手元の動作を丁寧に書く。"
        ),
    },
]
