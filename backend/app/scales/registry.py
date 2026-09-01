"""量表注册表：按 id 查询可用量表。"""
from app.scales.base import Scale
from app.scales.mht import mht
from app.scales.phq_a import phq_a
from app.scales.scared import scared
from app.scales.sdq import sdq

_REGISTRY: dict = {
    phq_a.scale_id: phq_a,
    scared.scale_id: scared,
    sdq.scale_id: sdq,
    mht.scale_id: mht,
}


def get_scale(scale_id: str) -> Scale:
    if scale_id not in _REGISTRY:
        raise KeyError(f"未知量表: {scale_id}，可用: {list(_REGISTRY)}")
    return _REGISTRY[scale_id]


def list_scales() -> list:
    return list(_REGISTRY.values())
