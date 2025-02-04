from pathlib import Path

import nonebot

nonebot.init(
    log_level="INFO",
    meme_stickers_data_dir=Path(__file__).parent,
)

nonebot.require("nonebot_plugin_meme_stickers")
