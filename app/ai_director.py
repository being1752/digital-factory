from __future__ import annotations

import base64
import json
import math
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .config import Settings
from .schemas import EmotionVector, Segment


DEFAULT_ANALYSIS = {
    "character_description": "人物外观等待视觉模型分析",
    "clothing_accessories": "服装与配饰等待视觉模型分析",
    "pose_description": "姿势与手部位置等待视觉模型分析",
    "background_lighting": "背景与光线等待视觉模型分析",
    "overall_style": "整体风格等待视觉模型分析",
    "visible_motion_space": "根据原图可见范围设计动作",
    "shot_type": "正面中近景坐姿",
    "visual_style": "温暖、专业、简洁",
    "baseline_expression": "自然浅笑",
    "persona": "专业、亲切、可信赖",
    "motion_level": 0.35,
    "safe_actions": ["自然手部摆动", "点头", "眼神变化", "侧头", "表情变化"],
    "avoid_actions": ["超出画面可见范围的动作", "与原始姿势无法衔接的动作", "遮挡面部"],
    "voice_suggestion": {"pace": "medium", "energy": 0.55, "warmth": 0.72},
}


class AIDirector:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def analyze_and_write(
        self,
        image_path: Path,
        original_script: str,
        purpose: str,
        audience: str,
        style: str,
    ) -> dict[str, Any]:
        locked_script = self._clean_script(original_script)
        if not self.settings.vision_enabled:
            analysis = self._fallback_analysis(image_path)
            if self.settings.ai_enabled:
                result = await self._chat(
                    self.settings.ai_text_model,
                    [
                        {
                            "role": "user",
                            "content": (
                                "LOCKED SCRIPT POLICY: The user's original script is the only content source. "
                                "Do not rewrite, shorten, expand, reorder, or alter facts, names, numbers, or qualifications. "
                                "Only design delivery style and the global TTS emotion vector.\n"
                                "你是数字人口播导演。当前没有可用的视觉模型，不能臆测图片中"
                                "人物的外观、服饰或背景。请依据已知图片元数据、用途、受众和"
                                "风格，优化口播文案。只输出 JSON 对象，包含 script、style、"
                                "emotion；emotion 必须包含 Happy、Angry、Sad、Fear、Hate、"
                                "Low、Surprise、Neutral，值均为 0 到 1。\n"
                                f"图片元数据：{json.dumps(analysis, ensure_ascii=False)}\n"
                                f"用途：{purpose or '品牌口播'}\n"
                                f"受众：{audience or '普通观众'}\n"
                                f"期望风格：{style or '专业、温和、可信赖'}\n"
                                f"原始文案：{original_script}"
                            ),
                        }
                    ],
                )
                emotion = EmotionVector.model_validate(result.get("emotion", {})).model_dump()
                return {
                    "ai_mode": "text_model_with_local_image_fallback",
                    "image_analysis": analysis,
                    "script": locked_script,
                    "style": str(result.get("style") or style or "专业、温和、有说服力"),
                    "emotion": emotion,
                }
            return {
                "ai_mode": "rules",
                "image_analysis": analysis,
                "script": locked_script,
                "style": style or "专业、温和、有说服力",
                "emotion": EmotionVector().model_dump(),
            }

        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        is_bigmodel_glm_vision = (
            "open.bigmodel.cn" in self.settings.vision_base_url
            and self.settings.ai_vision_model.lower().startswith("glm-")
        )
        image_value = encoded if is_bigmodel_glm_vision else f"data:{mime};base64,{encoded}"
        prompt = f"""
LOCKED SCRIPT POLICY: The supplied original script is the only source for spoken content. Do not rewrite, shorten, expand, reorder, or alter it. Analyze visible image facts and design delivery guidance around the locked script.
你是数字人口播导演。请先像专业选角与表演导演一样详细观察图片，再优化口播稿。
用途：{purpose or '品牌口播'}
受众：{audience or '普通观众'}
期望风格：{style or '专业、温和、可信赖'}
原始文案：{original_script}

只输出一个 JSON 对象，字段必须包括：
image_analysis 必须包含：
character_description：详细描述人物可见外观、发型、妆容、表情与气质；
clothing_accessories：详细描述服装颜色、材质、款式和配饰；
pose_description：详细描述坐姿或站姿、身体朝向、肩颈、双手位置；
background_lighting：详细描述环境、背景元素、光线方向、色温和氛围；
overall_style：总结画面的职业、生活或品牌风格以及适用场景；
visible_motion_space：根据景别和身体可见范围，说明头部、上身、手臂和手部可进行的自然动作空间；
shot_type, visual_style, baseline_expression, persona, motion_level(0-1),
safe_actions(数组，列出符合当前人物姿势和构图的自然动作，不要默认限制手部动作),
avoid_actions(数组，只列出与当前图片姿势、可见范围或画面连续性明显冲突的动作),
voice_suggestion(pace, energy, warmth)；
script: 优化后的完整口播稿，保留事实，不编造数字；
style；emotion: Happy, Angry, Sad, Fear, Hate, Low, Surprise, Neutral，所有值0到1。
所有图片描述必须来自实际可见信息，不识别具体身份，不凭空补充画面外内容。
动作判断的原则是“符合当前姿势且自然”，不是一律克制：如果手和手臂在画面中清晰可见，允许自然摆动、解释、展开等符合口播语义的动作。
""".strip()
        result = await self._chat(
            self.settings.ai_vision_model,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_value}},
                    ],
                }
            ],
            base_url=self.settings.vision_base_url,
            api_key=self.settings.vision_api_key,
            extra_body={"thinking": {"type": "enabled"}} if is_bigmodel_glm_vision else None,
        )
        analysis = {**DEFAULT_ANALYSIS, **result.get("image_analysis", {})}
        emotion = EmotionVector.model_validate(result.get("emotion", {})).model_dump()
        return {
            "ai_mode": "model",
            "image_analysis": analysis,
            "script": locked_script,
            "style": str(result.get("style") or style or "专业、温和、有说服力"),
            "emotion": emotion,
        }

    async def plan_segments(
        self,
        script: str,
        duration: float,
        analysis: dict[str, Any],
        style: str,
        alignment: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if alignment and alignment.get("windows"):
            windows = alignment["windows"]
        else:
            count = max(1, math.ceil((math.ceil(duration * 25) - 9) / 100))
            windows = [
                {
                    "index": i,
                    "start": round(i * 4, 3),
                    "end": round(min(duration, (i + 1) * 4), 3),
                    "spoken_text": text,
                    "sentence_context": text,
                    "starts_mid_sentence": False,
                    "ends_mid_sentence": False,
                    "speech_events": [],
                }
                for i, text in enumerate(self._text_windows(script, count))
            ]
        fallback = self._fallback_segments(windows, duration, analysis)
        if not self.settings.ai_enabled:
            return fallback

        compact = [
            {
                "index": window["index"],
                "start": window["start"],
                "end": window["end"],
                "window_text": window["spoken_text"],
                "full_sentence_context": window.get("sentence_context", ""),
                "starts_mid_sentence": window.get("starts_mid_sentence", False),
                "ends_mid_sentence": window.get("ends_mid_sentence", False),
                "speech_events": window.get("speech_events", []),
            }
            for window in windows
        ]
        prompt = f"""
COMPLETE LOCKED SCRIPT (the only spoken-content source; do not rewrite): {script}
Before planning each window, determine its semantic role in the complete script, such as greeting, factual explanation, transition, benefit emphasis, invitation, or closing. Motion and expression must serve that role instead of rotating mechanically by window index.
你是数字人口播动作导演。根据最终音频流的精确时间戳，为视频火车流的每个约4秒窗口设计自然、连续且符合语义的表演。
整体风格：{style}
图片约束：{json.dumps(analysis, ensure_ascii=False)}
窗口：{json.dumps(compact, ensure_ascii=False)}

只输出 JSON：{{"segments": [...]}}。每段必须含 index、start_state、end_state、action_prompt、motion_strength。
action_prompt 是可直接交给 InfiniteTalk 当前视频窗口的中文提示词，必须是一行，不得换行，不要编号，不要解释设计理由。
每行以“她对着前方说话”开头，随后用自然中文描述这一窗口中符合当前图片姿势的手部、头部、眼神和表情动作，例如：
“她对着前方说话，手部自然摆动，轻微皱眉，露出可爱的表情。”
每行控制在20到45个汉字，最多写一个自然动作和一个表情；不要引用正在说的台词，不要写“说到某词时”、秒数、先后步骤或动作设计理由。
动作丰富度由当前口播语义和图片可见动作空间决定，不要机械限制手部动作，也不要为了变化而加入与原姿势无法衔接的动作。
相邻窗口必须保持视频流连续；可以延续同一动作，也可以在语义变化时自然过渡，不要求每节都换动作。
starts_mid_sentence=true 表示开头正在承接上一窗口的同一句话，不能在窗口边界突然启动新动作或更换情绪；
ends_mid_sentence=true 表示句子还会进入下一窗口，结束姿态应当可自然延续。speech_events 中的 local_start/local_end
是本窗口内秒数：新句子在中途开始时，动作变化应在对应时间附近发生，但最终仍写成一行自然动作描述。
不要改变人物身份、服装、镜头和背景，不要在提示词中添加字幕、台词文本或时间轴说明。
""".strip()
        result = await self._chat(self.settings.ai_text_model, [{"role": "user", "content": prompt}])
        candidates = result.get("segments", [])
        by_index = {item.get("index"): item for item in candidates if isinstance(item, dict)}
        merged: list[dict[str, Any]] = []
        previous_end = fallback[0]["start_state"]
        for base in fallback:
            item = by_index.get(base["index"], {})
            payload = {
                **base,
                "start_state": previous_end,
                "end_state": str(item.get("end_state") or base["end_state"]),
                "action_prompt": self._action_prompt(
                    item.get("action_prompt") or base["action_prompt"]
                ),
                "motion_strength": item.get("motion_strength", base["motion_strength"]),
            }
            validated = Segment.model_validate(payload).model_dump()
            merged.append(validated)
            previous_end = validated["end_state"]
        return merged

    async def _chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_base_url = (base_url or self.settings.ai_base_url).rstrip("/")
        headers = {"Authorization": f"Bearer {api_key or self.settings.ai_api_key}"}
        body = {"model": model, "messages": messages, "temperature": 0.35}
        if extra_body:
            body.update(extra_body)
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{request_base_url}/chat/completions", headers=headers, json=body
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text.strip()
                if len(detail) > 1000:
                    detail = detail[:1000] + "..."
                raise RuntimeError(
                    f"AI API 请求失败：HTTP {response.status_code}，"
                    f"模型 {model}，地址 {request_base_url}。服务端响应：{detail or '<empty>'}"
                ) from exc
            content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return self._parse_json(str(content))

    @staticmethod
    def _parse_json(value: str) -> dict[str, Any]:
        value = value.strip()
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            start, end = value.find("{"), value.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("AI 没有返回有效 JSON")
            parsed = json.loads(value[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("AI 返回结果必须是 JSON 对象")
        return parsed

    @staticmethod
    def _clean_script(script: str) -> str:
        return re.sub(r"[ \t]+", " ", script.strip()).replace("\r\n", "\n")

    @staticmethod
    def _action_prompt(value: Any) -> str:
        prompt = re.sub(r"\s+", " ", str(value or "")).strip().strip('"“”')
        prompt = re.sub(r"([，。！？；：])\s+", r"\1", prompt)
        prompt = re.sub(r"^(?:第\s*\d+\s*(?:段|节)?[：:、.-]?\s*)", "", prompt)
        if not prompt.startswith("她对着前方说话"):
            prompt = f"她对着前方说话，{prompt.lstrip('，。 ')}"
        return prompt.rstrip("。") + "。"

    @staticmethod
    def _fallback_analysis(image_path: Path) -> dict[str, Any]:
        result = dict(DEFAULT_ANALYSIS)
        with Image.open(image_path) as image:
            width, height = image.size
        result["image_size"] = {"width": width, "height": height}
        result["orientation"] = "portrait" if height > width else "landscape"
        return result

    @staticmethod
    def _text_windows(script: str, count: int) -> list[str]:
        text = re.sub(r"\s+", "", script)
        if not text:
            return ["自然对镜头说话"] * count
        weights = [1.0 if "\u4e00" <= char <= "\u9fff" else 0.45 for char in text]
        total = sum(weights)
        boundaries = [total * i / count for i in range(1, count)]
        cuts: list[int] = []
        cumulative = 0.0
        boundary_index = 0
        punctuation = "，。！？；：,.!?;:"
        for index, weight in enumerate(weights, start=1):
            cumulative += weight
            if boundary_index >= len(boundaries) or cumulative < boundaries[boundary_index]:
                continue
            search_end = min(len(text), index + 8)
            cut = next((j + 1 for j in range(index - 1, search_end) if text[j] in punctuation), index)
            if cuts and cut <= cuts[-1]:
                cut = min(len(text), cuts[-1] + 1)
            cuts.append(cut)
            boundary_index += 1
        while len(cuts) < count - 1:
            cuts.append(cuts[-1] if cuts else len(text))
        result, start = [], 0
        for cut in cuts[: count - 1] + [len(text)]:
            result.append(text[start:cut] or "承接上一句自然说话")
            start = cut
        return result

    @staticmethod
    def _fallback_segments(
        windows: list[dict[str, Any]], duration: float, analysis: dict[str, Any]
    ) -> list[dict[str, Any]]:
        available = analysis.get("safe_actions") or DEFAULT_ANALYSIS["safe_actions"]
        if not isinstance(available, list) or not available:
            available = DEFAULT_ANALYSIS["safe_actions"]
        available = [str(action).strip() for action in available if str(action).strip()]
        if not available:
            available = DEFAULT_ANALYSIS["safe_actions"]
        actions = [
            (f"{available[0]}，眼神柔和，表情自然", "正视镜头，姿态自然", 0.34),
            (f"{available[1 % len(available)]}，眉眼随语义自然变化", "头部回到自然位置", 0.38),
            (f"{available[2 % len(available)]}，手部配合口播自然运动", "手部保持自然姿态", 0.42),
            (f"{available[3 % len(available)]}，露出亲切生动的表情", "恢复自然浅笑", 0.36),
        ]
        result: list[dict[str, Any]] = []
        state = analysis.get("baseline_expression", "正视镜头，自然浅笑")
        for index, window in enumerate(windows):
            spoken = window.get("spoken_text", "自然说话")
            action, end_state, strength = actions[index % len(actions)]
            events = window.get("speech_events", [])
            if window.get("starts_mid_sentence"):
                action = "延续上一节的姿态和表情，动作自然连贯"
                end_state = "保持当前自然姿态，继续完成这句话"
                strength = 0.2
            elif events and float(events[0].get("local_start", 0)) > 0.45:
                action = f"先自然停顿，随后开始说话并配合{available[index % len(available)]}"
                strength = 0.27
            if window.get("ends_mid_sentence"):
                action += "，结尾自然延续当前动作和说话情绪"
                end_state = "保持正视镜头并继续当前说话状态"
            if index == len(windows) - 1:
                action += "，结尾时恢复自然浅笑"
                end_state = "正视镜头，自然浅笑"
            result.append(
                Segment(
                    index=index,
                    start=round(index * 4.0, 3),
                    end=round(min(duration, (index + 1) * 4.0), 3),
                    spoken_text=spoken,
                    action_prompt=AIDirector._action_prompt(f"她对着前方说话，{action}"),
                    start_state=state,
                    end_state=end_state,
                    motion_strength=strength,
                    sentence_context=window.get("sentence_context", ""),
                    starts_mid_sentence=window.get("starts_mid_sentence", False),
                    ends_mid_sentence=window.get("ends_mid_sentence", False),
                    speech_events=window.get("speech_events", []),
                ).model_dump()
            )
            state = end_state
        return result
