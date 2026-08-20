from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

from .schemas import EMOTION_KEYS, EmotionVector, Segment


REQUIRED_LEGACY_TTS_NODES = {
    "easy downloadIndexTTSAndLoadModel",
    "easy indexTTSGenerateSimple",
    "easy indexTTSEmotionVector",
    "LoadAudio",
    "PreviewAudio",
}

REQUIRED_VOICE_CLONE_TTS_NODES = {
    "IndexTTS2Run",
    "LoadAudio",
    "AudioCrop",
    "PrimitiveStringMultiline",
    "SaveAudio",
}

REQUIRED_TTS_NODES = REQUIRED_LEGACY_TTS_NODES | REQUIRED_VOICE_CLONE_TTS_NODES


def required_tts_nodes(engine: str) -> set[str]:
    if engine == "indextts2_voice_clone":
        return REQUIRED_VOICE_CLONE_TTS_NODES
    return REQUIRED_LEGACY_TTS_NODES

REQUIRED_VIDEO_NODES = {
    "AudioEncoderEncode",
    "AudioEncoderLoader",
    "AudioSeparation",
    "AudioCrop",
    "CLIPVisionEncode",
    "CLIPVisionLoader",
    "CLIPLoader",
    "CLIPTextEncode",
    "DiffusionModelLoaderKJ",
    "ImageFromBatch",
    "ImageBatch",
    "KSampler",
    "LayerUtility: ImageScaleByAspectRatio V2",
    "LoadAudio",
    "LoadImage",
    "ModelPatchLoader",
    "VAEDecode",
    "VAELoader",
    "VHS_VideoCombine",
    "WanInfiniteTalkToVideo",
    "ConvertAny2List",
    "TextInput_",
    "easy indexAnything",
    "easy int",
    "easy promptLine",
    "easy showAnything",
}


TTS_MAX_SEED = 2**32 - 1

TRAIN_POSITIVE_INDEX_IDS = ("260", "266", "294", "322", "349", "413")
TRAIN_VIDEO_OUTPUT_IDS = ("19", "284", "316", "342", "369", "408")
TRAIN_SAMPLER_IDS = ("162", "281", "307", "335", "362", "401")


class WorkflowCompiler:
    def __init__(self, root: Path):
        self.root = root

    def compile_tts(
        self,
        script: str,
        reference_audio: str,
        emotion: dict[str, float],
        seed: int,
        engine: str = "indextts2_legacy",
        emotion_reference_audio: str | None = None,
    ) -> dict[str, Any]:
        if engine == "indextts2_voice_clone":
            if not emotion_reference_audio:
                raise ValueError("IndexTTS2 音色与情感克隆版需要情感参考音频")
            return self._compile_voice_clone_tts(
                script, reference_audio, emotion_reference_audio
            )
        if engine != "indextts2_legacy":
            raise ValueError(f"未知音频流程：{engine}")
        template_path = self.root / "indextts2-basic_emo_api.json"
        workflow = json.loads(template_path.read_text(encoding="utf-8"))
        workflow["27"]["inputs"]["text"] = script.strip()
        # IndexTTS2 declares this input as an unsigned 32-bit integer. Projects may
        # retain a larger seed for the video train, so only normalize the TTS copy.
        workflow["27"]["inputs"]["seed"] = int(seed) % (TTS_MAX_SEED + 1)
        # Release IndexTTS2 before local Whisper and InfiniteTalk use the GPU.
        workflow["27"]["inputs"]["unload_model"] = True
        workflow["29"]["inputs"]["audio"] = reference_audio
        validated = EmotionVector.model_validate(emotion).model_dump()
        for key in EMOTION_KEYS:
            workflow["47"]["inputs"][key] = validated[key]
        return workflow

    def _compile_voice_clone_tts(
        self,
        script: str,
        reference_audio: str,
        emotion_reference_audio: str,
    ) -> dict[str, Any]:
        template_path = self.root / "indextts2-voice-clone_api.json"
        workflow = json.loads(template_path.read_text(encoding="utf-8"))
        workflow["13"]["inputs"]["audio"] = reference_audio
        workflow["15"]["inputs"]["audio"] = emotion_reference_audio
        workflow["14"]["inputs"]["value"] = script.strip()
        workflow["12"]["inputs"]["unload_model"] = True
        workflow["17"]["inputs"]["filename_prefix"] = (
            "digital_factory/index_voice_clone"
        )
        return workflow

    def compile_video(
        self,
        image_name: str,
        audio_name: str,
        segments: list[dict[str, Any]],
        seed: int,
    ) -> dict[str, Any]:
        validated = [Segment.model_validate(item) for item in segments]
        if not validated:
            raise ValueError("至少需要一个视频动作段")

        verified_train = self.root / "infinitetalk-single-person-train_api.json"
        if not verified_train.exists():
            raise FileNotFoundError(f"缺少已验证的视频母版：{verified_train}")
        return self._compile_verified_train(
            verified_train, image_name, audio_name, validated, seed
        )

    def _compile_verified_train(
        self,
        template_path: Path,
        image_name: str,
        audio_name: str,
        segments: list[Segment],
        seed: int,
    ) -> dict[str, Any]:
        """Compile from workflow 1, the user's verified six-car train."""
        template = json.loads(template_path.read_text(encoding="utf-8"))
        template["221"]["inputs"]["image"] = image_name
        template["238"]["inputs"]["audio"] = audio_name
        template["254"]["inputs"]["text"] = "\n".join(
            self.compact_action_prompt(segment.action_prompt) for segment in segments
        )

        base_count = len(TRAIN_VIDEO_OUTPUT_IDS)
        for index in range(min(len(segments), base_count)):
            template[TRAIN_POSITIVE_INDEX_IDS[index]]["inputs"]["index"] = index

        # Six cars are the exact verified workflow. Preserve its topology, seeds,
        # and output scheduling; only project assets and action text are dynamic.
        if len(segments) == base_count:
            return template

        roots = list(TRAIN_VIDEO_OUTPUT_IDS[: min(len(segments), base_count)])
        if len(segments) > base_count:
            roots.extend(self._extend_verified_train(template, segments, seed))

        # A VHS_VideoCombine remains an executable output node even when
        # save_output is false: it still encodes a temporary preview.  Keeping
        # every stage output therefore re-encodes an ever-growing image batch
        # and creates avoidable VRAM/RAM churn between KSamplers.  Generated
        # trains need only the final accumulated video.
        for index, output_id in enumerate(roots):
            node = template[output_id]
            final = index == len(roots) - 1
            node["inputs"]["filename_prefix"] = (
                "digital_factory" if final else f"digital_factory_preview_{index + 1}"
            )
            node["inputs"]["save_output"] = final
            node["inputs"]["trim_to_audio"] = final

        if len(segments) > base_count:
            final_output_id = roots[-1]
            for output_id in roots[:-1]:
                template.pop(output_id, None)
            # Be explicit in case a cloned template retained preview settings.
            template[final_output_id]["inputs"]["save_output"] = True
            template[final_output_id]["inputs"]["trim_to_audio"] = True
            return template

        # Retain exactly the ancestors of the selected stage outputs. Later copied
        # train cars and unrelated UI preview branches must not enter the API graph.
        keep: set[str] = set()
        pending = list(roots)
        while pending:
            node_id = pending.pop()
            if node_id in keep or node_id not in template:
                continue
            keep.add(node_id)
            for value in template[node_id].get("inputs", {}).values():
                if (
                    isinstance(value, list)
                    and len(value) == 2
                    and isinstance(value[0], str)
                    and value[0] in template
                ):
                    pending.append(value[0])
        return {node_id: node for node_id, node in template.items() if node_id in keep}

    def _extend_verified_train(
        self,
        workflow: dict[str, Any],
        segments: list[Segment],
        seed: int,
    ) -> list[str]:
        """Append cars by cloning the sixth car from verified workflow 1."""
        accumulated: list[Any] = ["411", 0]
        output_ids: list[str] = []
        base_count = len(TRAIN_VIDEO_OUTPUT_IDS)

        for index in range(base_count, len(segments)):
            start = 1000 + (index - base_count) * 10
            index_id = str(start)
            preview_id = str(start + 1)
            negative_id = str(start + 2)
            positive_id = str(start + 3)
            generator_id = str(start + 4)
            sampler_id = str(start + 5)
            decode_id = str(start + 6)
            crop_id = str(start + 7)
            batch_id = str(start + 8)
            output_id = str(start + 9)

            workflow[index_id] = copy.deepcopy(workflow["413"])
            workflow[index_id]["inputs"]["index"] = index

            workflow[preview_id] = copy.deepcopy(workflow["388"])
            workflow[preview_id]["inputs"]["source"] = [index_id, 0]

            workflow[negative_id] = copy.deepcopy(workflow["387"])

            workflow[positive_id] = copy.deepcopy(workflow["389"])
            workflow[positive_id]["inputs"]["text"] = [index_id, 0]

            workflow[generator_id] = copy.deepcopy(workflow["409"])
            workflow[generator_id]["inputs"]["positive"] = [positive_id, 0]
            workflow[generator_id]["inputs"]["negative"] = [negative_id, 0]
            workflow[generator_id]["inputs"]["previous_frames"] = accumulated

            workflow[sampler_id] = copy.deepcopy(workflow["401"])
            workflow[sampler_id]["inputs"].update(
                {
                    "seed": int(seed + index * 7919) % (2**63 - 1),
                    "model": [generator_id, 0],
                    "positive": [generator_id, 1],
                    "negative": [generator_id, 2],
                    "latent_image": [generator_id, 3],
                }
            )

            workflow[decode_id] = copy.deepcopy(workflow["410"])
            workflow[decode_id]["inputs"]["samples"] = [sampler_id, 0]

            workflow[crop_id] = copy.deepcopy(workflow["412"])
            workflow[crop_id]["inputs"]["batch_index"] = [generator_id, 4]
            workflow[crop_id]["inputs"]["image"] = [decode_id, 0]

            workflow[batch_id] = copy.deepcopy(workflow["411"])
            workflow[batch_id]["inputs"]["image1"] = accumulated
            workflow[batch_id]["inputs"]["image2"] = [crop_id, 0]
            accumulated = [batch_id, 0]

            workflow[output_id] = copy.deepcopy(workflow["408"])
            workflow[output_id]["inputs"]["images"] = accumulated
            output_ids.append(output_id)

        return output_ids

    @staticmethod
    def video_segment_node_map(
        workflow: dict[str, Any], segment_count: int
    ) -> dict[str, int]:
        """Map executable KSampler node ids to their one-based train car."""
        node_ids = list(TRAIN_SAMPLER_IDS[: min(segment_count, len(TRAIN_SAMPLER_IDS))])
        for index in range(len(TRAIN_SAMPLER_IDS), segment_count):
            node_ids.append(str(1000 + (index - len(TRAIN_SAMPLER_IDS)) * 10 + 5))
        return {
            node_id: index + 1
            for index, node_id in enumerate(node_ids)
            if node_id in workflow and workflow[node_id].get("class_type") == "KSampler"
        }

    @staticmethod
    def compact_action_prompt(value: str, max_length: int = 36) -> str:
        """Clean a prompt without rewriting the user's approved direction."""
        prompt = re.sub(r"\s+", " ", str(value or "")).strip().strip('"“”')
        prompt = re.sub(r"(?:她|他|人物)对着(?:前方|镜头)说话", "人物对着镜头说话", prompt)
        prompt = re.sub(r"^(?:人物对着镜头说话[。；;，, ]*){2,}", "人物对着镜头说话，", prompt)
        if not prompt:
            return "人物对着镜头说话。"
        if not prompt.startswith("人物对着镜头说话"):
            prompt = "人物对着镜头说话，" + prompt.lstrip("，。；;,. ")
        return prompt.rstrip("。") + "。"

    @staticmethod
    def expected_segment_count(duration: float) -> int:
        return max(1, math.ceil((math.ceil(duration * 25) - 9) / 100))
