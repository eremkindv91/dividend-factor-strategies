from .banks import PACK as BANKS
from .developers import PACK as DEVELOPERS
from .market_sectors import INDEX_ONLY_PACKS
from .oil_gas import PACK as OIL_GAS
from .steel import PACK as STEEL

PACKS = {
    pack["pack_id"]: pack
    for pack in (OIL_GAS, STEEL, BANKS, DEVELOPERS, *INDEX_ONLY_PACKS)
}

__all__ = ["PACKS"]
