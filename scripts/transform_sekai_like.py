import asyncio
import json
import math
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TypeVar, Union
from typing_extensions import ParamSpec, TypeAlias

from cookit import with_semaphore
from cookit.pyd import CamelAliasModel, type_validate_json
from httpx import AsyncClient
from nonebot_plugin_meme_stickers.models import (
    CHECKSUM_FILENAME,
    MANIFEST_FILENAME,
    ChecksumDict,
    RGBAColorTuple,
    StickerGridSetting,
    StickerInfoOptionalParams,
    StickerPackConfig,
    StickerPackManifest,
    StickerParamsOptional,
)
from nonebot_plugin_meme_stickers.sticker_pack import (
    calc_checksum,
    calc_checksum_from_file,
    dump_readable_model,
)
from nonebot_plugin_meme_stickers.utils import op_retry
from pydantic import BaseModel
from rich.progress import Progress
from yarl import URL


class CharacterDefaultText(BaseModel):
    text: str
    x: int
    y: int
    r: float
    s: int


class SekaiCharacter(CamelAliasModel):
    id: str
    name: str
    character: str
    img: str
    color: str
    default_text: CharacterDefaultText


class ArcaeaCharacter(CamelAliasModel):
    id: str
    name: str
    character: str
    img: str
    fill_color: str
    stroke_color: str
    default_text: CharacterDefaultText


Character: TypeAlias = Union[SekaiCharacter, ArcaeaCharacter]
Characters: TypeAlias = Union[list[SekaiCharacter], list[ArcaeaCharacter]]

P = ParamSpec("P")
R = TypeVar("R")

ResDownloadFinishCallback: TypeAlias = Callable[[str, str], None]
"""(path, sha256) -> None"""

CharsGotCallback: TypeAlias = Callable[[Characters], None]


def normalize_character_name(name: str) -> str:
    return f"{name[0].upper()}{name[1:]}"


def to_local_path(char: Character) -> str:
    return f"{normalize_character_name(char.character)}/{URL(char.img).name}"


def create_sem() -> asyncio.Semaphore:
    return asyncio.Semaphore(8)


async def prepare_resources(
    chars: Characters,
    res_base_url: URL,
    download_path: Path,
    sem: asyncio.Semaphore,
    finish_callback: ResDownloadFinishCallback,
) -> ChecksumDict:
    """return map of file path and sha256 hashes"""

    @with_semaphore(sem)
    @op_retry()
    async def download_task(cli: AsyncClient, char: Character) -> tuple[str, str]:
        """return checksum"""
        url = res_base_url / char.img
        async with cli.stream("GET", str(url)) as resp:
            resp.raise_for_status()
            content = await resp.aread()

        checksum = calc_checksum(content)

        relative_path = to_local_path(char)
        file_path = download_path / to_local_path(char)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)

        finish_callback(relative_path, checksum)
        return relative_path, checksum

    async with AsyncClient() as cli:
        result = await asyncio.gather(*(download_task(cli, c) for c in chars))
    return dict(result)


def web_hex_to_color_tuple(color: str) -> RGBAColorTuple:
    color = color.upper().removeprefix("#")

    if len(color) == 3:
        color = "".join((*(c * 2 for c in color), "FF"))
    elif len(color) == 4:
        color = "".join((*(c * 2 for c in color[1:]), color[0] * 2))
    elif len(color) == 6:
        color = f"{color}FF"
    elif len(color) == 8:
        color = f"{color[2:]}{color[:2]}"
    else:
        raise ValueError("Invalid color format")

    return tuple(int(color[i : i + 2], 16) for i in range(0, 8, 2))  # type: ignore


WIDTH = 296
HEIGHT = 256
STROKE_COLOR: RGBAColorTuple = (255, 255, 255, 255)
STROKE_FACTOR = 9 / 38


async def transform_manifest(
    chars: Characters,
    base_manifest: Optional[StickerPackManifest] = None,
) -> StickerPackManifest:
    return StickerPackManifest(
        version=(
            base_manifest.version  # + 1
            if base_manifest
            else 1
        ),
        name=base_manifest.name if base_manifest else "name",
        description=base_manifest.description if base_manifest else "",
        default_config=(
            base_manifest.default_config if base_manifest else StickerPackConfig()
        ),
        default_sticker_params=(
            base_manifest.default_sticker_params
            if base_manifest
            else StickerParamsOptional(
                width=WIDTH,
                height=HEIGHT,
                text_align="center",
                font_style="normal",
                font_families=[],
                stroke_color=(
                    STROKE_COLOR if isinstance(chars[0], SekaiCharacter) else None
                ),
                stroke_width_factor=STROKE_FACTOR,
            )
        ),
        sticker_grid=(
            base_manifest.sticker_grid if base_manifest else StickerGridSetting()
        ),
        sample_sticker=base_manifest.sample_sticker if base_manifest else None,
        external_fonts=base_manifest.external_fonts if base_manifest else [],
        stickers=[
            StickerInfoOptionalParams(
                name=char.name,
                category=normalize_character_name(char.character),
                params=StickerParamsOptional(
                    base_image=to_local_path(char),
                    text=char.default_text.text,
                    text_x=char.default_text.x,
                    text_y=char.default_text.y,
                    text_rotate_degrees=math.degrees(char.default_text.r / 10),
                    text_color=(
                        web_hex_to_color_tuple(char.color)
                        if isinstance(char, SekaiCharacter)
                        else web_hex_to_color_tuple(char.fill_color)
                    ),
                    stroke_color=(
                        web_hex_to_color_tuple(char.stroke_color)
                        if isinstance(char, ArcaeaCharacter)
                        else None
                    ),
                    font_size=char.default_text.s,
                ),
            )
            for char in chars
        ],
    )


async def transform_sekai_like(
    characters_json_url: str,
    res_base_url: str,
    target_path: Path,
    chars_got_callback: CharsGotCallback,
    finish_callback: ResDownloadFinishCallback,
):
    manifest_path = target_path / MANIFEST_FILENAME
    original_manifest = (
        type_validate_json(StickerPackManifest, manifest_path.read_text("u8"))
        if manifest_path.exists()
        else None
    )

    async with AsyncClient() as cli:
        chars = type_validate_json(
            Characters,
            ((await op_retry()(cli.get)(characters_json_url)).raise_for_status().text),
        )
    chars_got_callback(chars)

    sem = create_sem()
    res_base_url_obj = URL(res_base_url)
    checksum = await prepare_resources(
        chars,
        res_base_url_obj,
        target_path,
        sem,
        finish_callback,
    )
    if original_manifest:
        for f in original_manifest.external_fonts:
            checksum[f.path] = calc_checksum_from_file(target_path / f.path)
    checksum = dict(sorted(checksum.items(), key=lambda x: x[0].split("/")))

    new_manifest = await transform_manifest(chars, original_manifest)
    manifest_path.write_text(
        dump_readable_model(new_manifest, exclude_unset=True, exclude_defaults=True),
        "u8",
    )

    checksum_path = target_path / CHECKSUM_FILENAME
    checksum_path.write_text(
        json.dumps(checksum, ensure_ascii=False, indent=2),
        "u8",
    )


@dataclass
class TransformTaskConfig:
    name: str
    characters_json_url: str
    res_base_url: str


async def _main() -> int:
    root_path = Path(__file__).parent.parent

    task_configs = [
        TransformTaskConfig(
            "pjsk",
            (
                "https://raw.githubusercontent.com/TheOriginalAyaka/sekai-stickers"
                "/refs/heads/main/src/characters.json"
            ),
            (
                "https://raw.githubusercontent.com/TheOriginalAyaka/sekai-stickers"
                "/refs/heads/main/public/img"
            ),
        ),
        TransformTaskConfig(
            "arcaea",
            (
                "https://raw.githubusercontent.com/Rosemoe/arcaea-stickers"
                "/refs/heads/main/src/characters.json"
            ),
            (
                "https://raw.githubusercontent.com/Rosemoe/arcaea-stickers"
                "/refs/heads/main/public/img"
            ),
        ),
    ]

    progress = Progress(
        *Progress.get_default_columns(),
        "[yellow]{task.completed}/{task.total}",
    )

    async def transform_task(cfg: TransformTaskConfig):
        task_id = progress.add_task(cfg.name, start=False, total=0)
        try:
            await transform_sekai_like(
                cfg.characters_json_url,
                cfg.res_base_url,
                root_path / cfg.name,
                lambda chars: (
                    progress.update(task_id, total=len(chars))
                    or progress.start_task(task_id)
                ),
                lambda path, _: progress.update(
                    task_id,
                    description=f"{cfg.name}: {path}",
                    advance=1,
                ),
            )
        except Exception:
            traceback.print_exc()
            progress.update(task_id, description=f"{cfg.name}: Error")
            raise
        progress.stop_task(task_id)

    with progress:
        excs = await asyncio.gather(
            *(transform_task(cfg) for cfg in task_configs),
            return_exceptions=True,
        )
        if any(excs):
            return 1

    return 0


def main():
    sys.exit(asyncio.run(_main()))
