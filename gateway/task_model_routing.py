"""Deterministic, fail-closed task-to-model routing for gateway turns.

This router is deliberately lexical rather than LLM-based: selecting a model
must not add a second model call, and uncertainty must stay on the gateway's
primary model.  It only returns a non-default route for narrowly defined,
low-risk task shapes.  The gateway retains explicit `/model` / `/moa` choices
and channel overrides as higher-priority controls.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping


def _decode_json_scalar(value: Any) -> Any:
    """Decode JSON passed through ``hermes config set`` as a YAML scalar."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


@dataclass(frozen=True)
class TaskModelRoute:
    """A configured model route selected for one inbound task."""

    name: str
    provider: str
    model: str
    reason: str


# These requests either need live data/tools, create an external side effect,
# or carry a material business/safety consequence.  They must never be sent to
# the fast low-risk lane merely because they also ask for a summary/rewrite.
_HIGH_RISK_OR_TOOL_TERMS = (
    "审批", "批准", "通过", "驳回", "付款", "汇款", "转账", "付款", "合同", "股权",
    "融资", "并购", "投资", "报价", "定价", "删除", "重置", "发布", "外发", "发送",
    "授权", "密码", "密钥", "token", "隐私", "身份证", "银行卡", "医疗", "诊断",
    "用药", "处方", "oa", "费控", "帆软", "nas", "服务器", "docker", "日志",
    "查一下", "查询", "检索", "搜索", "打开", "登录", "配置", "部署", "运行", "测试",
    "修复", "改代码", "提交", "上线", "同步到生产", "生产环境", "实际数据", "公开资料",
)

_FAST_TERMS = (
    "总结", "摘要", "压缩", "提炼", "归纳", "改写", "润色", "翻译", "校对",
    "格式化", "整理成", "提取", "分类", "改成", "转成", "列成", "缩成",
)

_CONTENT_TERMS = (
    "文案", "口播", "短视频脚本", "视频脚本", "标题", "slogan", "广告语",
    "公众号", "推文", "朋友圈", "海报", "新闻稿", "开场白", "结束语", "改得更有梗",
)

_DECISION_TERMS = (
    "重大决策", "决策会诊", "董事会", "战略选择", "商业模式", "股权安排", "合资",
    "融资", "并购", "组织调整", "定价策略", "重大方案",
)

_CODE_REVIEW_TERMS = (
    "代码审查", "code review", "权限链路", "安全审查", "数据库迁移", "跨模块审查",
    "架构审查", "高风险改动",
)


def _route_from_config(routes: Mapping[str, Any], name: str, reason: str) -> TaskModelRoute | None:
    raw = _decode_json_scalar(routes.get(name))
    if not isinstance(raw, Mapping):
        return None
    provider = str(raw.get("provider") or "").strip()
    model = str(raw.get("model") or "").strip()
    if not provider or not model:
        return None
    return TaskModelRoute(name=name, provider=provider, model=model, reason=reason)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def resolve_task_model_route(
    message: str,
    config: Mapping[str, Any] | None,
    *,
    platform: str = "",
) -> TaskModelRoute | None:
    """Return a safe per-turn route, or ``None`` to retain the primary model.

    The function has no I/O and intentionally fails closed.  A malformed or
    missing configuration, an unknown platform, a slash command, or any risky
    request leaves model selection untouched.
    """
    if not isinstance(config, Mapping):
        return None
    policy = config.get("task_model_routing")
    if not isinstance(policy, Mapping) or not bool(policy.get("enabled")):
        return None

    allowed_platforms = _decode_json_scalar(policy.get("platforms"))
    if isinstance(allowed_platforms, (list, tuple, set)):
        normalized = {str(item).strip().lower() for item in allowed_platforms}
        if normalized and str(platform).strip().lower() not in normalized:
            return None

    text = str(message or "").strip()
    if not text or text.startswith("/"):
        return None
    normalized = text.lower()
    routes = policy.get("routes")
    if not isinstance(routes, Mapping):
        return None

    # High-value review/decision requests are deliberately evaluated before
    # the general high-risk guard: they are safe because they route upward to
    # a configured MoA preset rather than downward to a cheaper model.
    if _has_any(normalized, _CODE_REVIEW_TERMS):
        return _route_from_config(routes, "code_review", "explicit complex code/risk review")
    if _has_any(normalized, _DECISION_TERMS):
        return _route_from_config(routes, "decision", "explicit high-stakes decision")

    if _has_any(normalized, _HIGH_RISK_OR_TOOL_TERMS):
        return None

    # Content stays on the stronger writing route only when the request is a
    # self-contained draft.  Requests grounded in live research/facts have
    # already been rejected above and remain on Terra.
    if _has_any(normalized, _CONTENT_TERMS):
        return _route_from_config(routes, "content", "self-contained Chinese content draft")

    # The fast lane is intentionally narrow.  A size limit prevents silently
    # downgrading long documents whose nuance/context need the primary model.
    max_chars = policy.get("quick_max_chars", 1600)
    try:
        max_chars = max(1, int(max_chars))
    except (TypeError, ValueError):
        max_chars = 1600
    if len(text) <= max_chars and _has_any(normalized, _FAST_TERMS):
        return _route_from_config(routes, "quick", "short low-risk transform")

    return None
