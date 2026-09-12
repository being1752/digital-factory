from __future__ import annotations

import unittest
import asyncio
import copy
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.ai_director import AIDirector, normalize_image_analysis
from app.alignment import SpeechAlignmentService
from app.audio import audio_duration
from app.config import settings
from app.comfyui import ComfyUIClient
from app.production_queue import ProductionQueue
from app.music_library import (
    MusicLibraryError,
    available_music_files,
    copy_music_by_name,
    copy_random_music,
)
from app.postproduction import BackgroundMusicMixer
from app.repository import ProjectRepository
from app.schemas import ProjectCreate
from app.subtitles import SubtitleDocument, subtitle_display_text
from app.workflows import TRAIN_SAMPLER_IDS, TRAIN_VIDEO_OUTPUT_IDS, WorkflowCompiler


ROOT = Path(__file__).resolve().parent.parent

COMPLETE_IMAGE_ANALYSIS = {
    "character_description": "正面人物，神情自然",
    "clothing_accessories": "浅色上衣，佩戴简洁配饰",
    "pose_description": "正面坐姿，肩颈放松，双手可见",
    "background_lighting": "室内背景，正面柔光",
    "overall_style": "专业亲切的品牌口播风格",
    "visible_motion_space": "头部、上身和画面内双手均可自然活动",
    "shot_type": "正面中近景",
    "visual_style": "自然柔和",
    "baseline_expression": "自然浅笑",
    "persona": "专业亲切",
    "motion_level": 0.45,
    "voice_suggestion": {"pace": "medium", "energy": 0.6, "warmth": 0.7},
    "safe_actions": ["自然摆手", "轻微点头"],
    "avoid_actions": ["手臂伸出画面"],
}


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = WorkflowCompiler(ROOT)

    def test_reference_m4s_duration(self) -> None:
        reference = ROOT / "40049511080-1-30280.m4s"
        if not reference.is_file():
            self.skipTest("optional reference audio is not present")
        duration = audio_duration(reference)
        self.assertAlmostEqual(duration, 53.453, places=3)

    def test_tts_compiler_replaces_inputs(self) -> None:
        workflow = self.compiler.compile_tts(
            "测试口播", "remote_voice.m4s", {"Neutral": 0.8, "Happy": 0.1}, 123
        )
        self.assertEqual(workflow["27"]["inputs"]["text"], "测试口播")
        self.assertEqual(workflow["27"]["inputs"]["seed"], 123)
        self.assertTrue(workflow["27"]["inputs"]["unload_model"])
        self.assertEqual(workflow["29"]["inputs"]["audio"], "remote_voice.m4s")
        self.assertEqual(workflow["47"]["inputs"]["Neutral"], 0.8)

    def test_tts_seed_is_normalized_to_unsigned_32_bit(self) -> None:
        large_seed = 8383143110833373
        workflow = self.compiler.compile_tts(
            "测试口播", "remote_voice.m4s", {"Neutral": 0.8}, large_seed
        )
        normalized = workflow["27"]["inputs"]["seed"]
        self.assertEqual(normalized, large_seed % (2**32))
        self.assertGreaterEqual(normalized, 0)
        self.assertLessEqual(normalized, 4294967295)

    def test_voice_clone_tts_uses_separate_voice_and_emotion_audio_without_qwen(self) -> None:
        workflow = self.compiler.compile_tts(
            "新版双音频克隆口播",
            "uploaded_voice.m4a",
            {"Happy": 1.0},
            123,
            "indextts2_voice_clone",
            "uploaded_emotion.wav",
        )
        self.assertEqual(set(workflow), {"12", "13", "14", "15", "17", "37", "38"})
        self.assertEqual(workflow["13"]["inputs"]["audio"], "uploaded_voice.m4a")
        self.assertEqual(workflow["15"]["inputs"]["audio"], "uploaded_emotion.wav")
        self.assertEqual(workflow["14"]["inputs"]["value"], "新版双音频克隆口播")
        self.assertTrue(workflow["12"]["inputs"]["unload_model"])
        self.assertEqual(workflow["12"]["inputs"]["emo_audio_prompt"], ["38", 0])
        self.assertNotIn("emo_text", workflow["12"]["inputs"])
        self.assertFalse(
            any(
                node["class_type"] == "FB_Qwen3TTSVoiceClone"
                for node in workflow.values()
            )
        )

    def test_voice_clone_tts_requires_emotion_reference_audio(self) -> None:
        with self.assertRaisesRegex(ValueError, "情感参考音频"):
            self.compiler.compile_tts(
                "测试", "uploaded_voice.m4a", {}, 123, "indextts2_voice_clone"
            )

    def test_project_create_accepts_two_independent_tts_engines(self) -> None:
        legacy = ProjectCreate(original_script="测试")
        clone = ProjectCreate(
            original_script="测试",
            tts_engine="indextts2_voice_clone",
            auto_run=True,
            expect_emotion_voice_upload=True,
        )
        self.assertEqual(legacy.tts_engine, "indextts2_legacy")
        self.assertEqual(clone.tts_engine, "indextts2_voice_clone")
        self.assertFalse(legacy.auto_run)
        self.assertTrue(legacy.bgm_enabled)
        self.assertEqual(legacy.bgm_source, "library_random")
        self.assertTrue(clone.auto_run)
        self.assertTrue(clone.expect_emotion_voice_upload)

    def test_project_create_accepts_background_music_settings(self) -> None:
        project = ProjectCreate(
            original_script="测试",
            bgm_enabled=True,
            bgm_volume=0.32,
            bgm_ducking=True,
            bgm_fade_in=1.2,
            bgm_fade_out=2.5,
            expect_bgm_upload=True,
        )
        self.assertTrue(project.bgm_enabled)
        self.assertAlmostEqual(project.bgm_volume, 0.32)
        self.assertTrue(project.bgm_ducking)
        self.assertTrue(project.expect_bgm_upload)

    def test_project_create_accepts_random_music_without_upload(self) -> None:
        project = ProjectCreate(
            original_script="测试",
            bgm_enabled=True,
            bgm_source="library_random",
            expect_bgm_upload=False,
        )
        self.assertEqual(project.bgm_source, "library_random")
        self.assertFalse(project.expect_bgm_upload)

    def test_create_project_random_music_uses_configured_directory(self) -> None:
        from app import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "custom-music"
            library.mkdir()
            (library / "selected.mp3").write_bytes(b"selected-music")
            repository = ProjectRepository(root / "data" / "jobs.db")
            repository.set_setting("music_library_path", str(library))
            configured = replace(settings, root=root, data_dir=root / "data")
            with (
                patch.object(main, "repository", repository),
                patch.object(main, "settings", configured),
            ):
                project = main.create_default_project(
                    ProjectCreate(
                        original_script="测试",
                        bgm_enabled=True,
                        bgm_source="library_random",
                        expect_image_upload=True,
                        expect_voice_upload=True,
                    )
                )
            self.assertEqual(project["bgm_source"], "library_random")
            self.assertEqual(project["bgm_name"], "selected.mp3")
            self.assertEqual(Path(project["bgm_path"]).read_bytes(), b"selected-music")

    def test_random_music_copies_one_supported_file_into_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "my-music"
            input_dir = root / "job" / "input"
            library.mkdir()
            (library / "a.txt").write_text("ignore", encoding="utf-8")
            (library / "b.mp3").write_bytes(b"music-b")
            (library / "c.wav").write_bytes(b"music-c")
            self.assertEqual([path.name for path in available_music_files(library)], ["b.mp3", "c.wav"])
            destination, source_name = copy_random_music(
                library, input_dir, chooser=lambda candidates: candidates[-1]
            )
            self.assertEqual(source_name, "c.wav")
            self.assertEqual(destination.name, "background_music_library.wav")
            self.assertEqual(destination.read_bytes(), b"music-c")

    def test_named_music_preview_selection_is_safe_and_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "music"
            input_dir = root / "job" / "input"
            library.mkdir()
            (library / "selected.mp3").write_bytes(b"previewed-music")
            destination, source_name = copy_music_by_name(
                library, input_dir, "selected.mp3"
            )
            self.assertEqual(source_name, "selected.mp3")
            self.assertEqual(destination.read_bytes(), b"previewed-music")
            with self.assertRaises(MusicLibraryError):
                copy_music_by_name(library, input_dir, "../selected.mp3")

    def test_image_analysis_completes_auxiliary_fields_and_requires_core_details(self) -> None:
        normalized = normalize_image_analysis(
            {
                "character": "人物外观",
                "clothing": "服饰信息",
                "pose": "正面坐姿",
                "background": "室内柔光",
                "style": "专业风格",
            }
        )
        self.assertEqual(normalized["clothing_accessories"], "服饰信息")
        self.assertIn("正面坐姿", normalized["visible_motion_space"])
        self.assertTrue(normalized["shot_type"])
        self.assertIn("正面坐姿", normalized["safe_actions"][0])
        self.assertIn(normalized["visible_motion_space"], normalized["avoid_actions"][0])
        self.assertIsInstance(normalized["motion_level"], float)
        self.assertIsInstance(normalized["voice_suggestion"]["energy"], float)
        with self.assertRaisesRegex(ValueError, "clothing_accessories"):
            normalize_image_analysis(
                {
                    key: value
                    for key, value in COMPLETE_IMAGE_ANALYSIS.items()
                    if key != "clothing_accessories"
                }
            )

    def test_no_bgm_download_prefers_subtitled_video(self) -> None:
        from app import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.mp4"
            subtitled = root / "subtitled.mp4"
            mixed = root / "mixed.mp4"
            for path in (raw, subtitled, mixed):
                path.write_bytes(path.name.encode("utf-8"))
            repository = ProjectRepository(root / "jobs.db")
            repository.create(
                {
                    "id": "video-job",
                    "title": "测试",
                    "raw_video_path": str(raw),
                    "subtitle_video_path": str(subtitled),
                    "bgm_video_path": str(mixed),
                    "video_path": str(mixed),
                }
            )
            with patch.object(main, "repository", repository):
                public = main.public_project(repository.get("video-job") or {})
                response = asyncio.run(main.project_file("video-job", "video_no_bgm"))
            self.assertTrue(public["has_no_bgm_video"])
            self.assertEqual(Path(response.path), subtitled)
    def test_rebuilding_bgm_uses_a_new_file_when_previous_video_is_open(self) -> None:
        from app.orchestrator import TaskRunner

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project_dir = root / "job"
                output_dir = project_dir / "output"
                output_dir.mkdir(parents=True)
                source = output_dir / "video_subtitled.mp4"
                speech = project_dir / "speech.flac"
                music = project_dir / "music.mp3"
                for path in (source, speech, music):
                    path.write_bytes(b"source")
                repository = ProjectRepository(root / "jobs.db")
                repository.create(
                    {
                        "id": "bgm-job",
                        "title": "测试",
                        "project_dir": str(project_dir),
                        "subtitle_enabled": True,
                        "subtitle_video_path": str(source),
                        "raw_video_path": str(source),
                        "audio_path": str(speech),
                        "audio_duration": 5.0,
                        "bgm_enabled": True,
                        "bgm_path": str(music),
                    }
                )
                runner = TaskRunner(
                    settings,
                    repository,
                    None,  # type: ignore[arg-type]
                    None,  # type: ignore[arg-type]
                    None,  # type: ignore[arg-type]
                )
                destinations: list[Path] = []

                class FakeMixer:
                    async def mix(self, _video, _speech, _music, destination, **_options):
                        destination.write_bytes(b"mixed")
                        destinations.append(destination)
                        return destination

                runner.music_mixer = FakeMixer()  # type: ignore[assignment]
                await runner.mix_background_music("bgm-job")
                first = Path((repository.get("bgm-job") or {})["bgm_video_path"])
                # Keep the first output present, matching a browser that still has it open.
                await runner.mix_background_music("bgm-job")
                second = Path((repository.get("bgm-job") or {})["bgm_video_path"])
                self.assertNotEqual(first, second)
                self.assertTrue(first.is_file())
                self.assertTrue(second.is_file())
                self.assertRegex(first.name, r"^video_with_bgm_[0-9a-f]{8}\.mp4$")
                self.assertEqual(destinations, [first, second])

        asyncio.run(scenario())

    def test_background_music_command_loops_ducks_and_copies_video(self) -> None:
        mixer = BackgroundMusicMixer("python")
        command = mixer.command(
            Path("raw.mp4"),
            Path("speech.wav"),
            Path("music.mp3"),
            Path("final.mp4"),
            duration=12.5,
            volume=0.25,
            ducking=True,
            fade_in=1.5,
            fade_out=2.0,
        )
        joined = " ".join(command)
        self.assertIn("-stream_loop -1", joined)
        self.assertIn("loudnorm=I=-14:TP=-1:LRA=7", joined)
        self.assertIn("threshold=0.08:ratio=3:attack=50:release=400", joined)
        self.assertIn("normalize=0", joined)
        self.assertIn("afade=t=in:st=0:d=1.500", joined)
        self.assertIn("afade=t=out:st=10.500:d=2.000", joined)
        self.assertIn("-c:v copy", joined)
        self.assertIn("-t 12.500", joined)

    def test_video_compiler_builds_dynamic_chain(self) -> None:
        segments = [
            {
                "index": i,
                "start": i * 4,
                "end": (i + 1) * 4,
                "spoken_text": f"第{i + 1}段",
                "action_prompt": f"自然说话，第{i + 1}段轻微动作",
            }
            for i in range(8)
        ]
        workflow = self.compiler.compile_video("portrait.png", "speech.flac", segments, 42)
        generators = [node for node in workflow.values() if node["class_type"] == "WanInfiniteTalkToVideo"]
        self.assertEqual(len(generators), 8)
        self.assertTrue(all(node["inputs"]["audio_encoder_output_1"] == ["4", 0] for node in generators))
        self.assertIn("previous_frames", generators[-1]["inputs"])
        video_outputs = [
            node for node in workflow.values() if node["class_type"] == "VHS_VideoCombine"
        ]
        self.assertEqual(len(video_outputs), 1)
        self.assertTrue(video_outputs[0]["inputs"]["save_output"])
        self._assert_references_exist(workflow)

    def test_video_train_over_eight_segments_keeps_only_final_output(self) -> None:
        segments = [
            {
                "index": i,
                "start": i * 4,
                "end": (i + 1) * 4,
                "spoken_text": f"第{i + 1}段",
                "action_prompt": f"她对着前方说话，第{i + 1}段自然动作。",
            }
            for i in range(9)
        ]
        workflow = self.compiler.compile_video("portrait.png", "speech.flac", segments, 42)
        outputs = [
            node for node in workflow.values() if node["class_type"] == "VHS_VideoCombine"
        ]
        self.assertEqual(len(outputs), 1)
        self.assertTrue(outputs[0]["inputs"]["save_output"])
        self._assert_references_exist(workflow)

    def test_six_car_graph_matches_verified_workflow_one(self) -> None:
        template_path = ROOT / "infinitetalk-single-person-train_api.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        segments = [
            {
                "index": i,
                "start": i * 4,
                "end": (i + 1) * 4,
                "spoken_text": f"第{i + 1}段",
                "action_prompt": f"她对着前方说话，第{i + 1}段自然动作。",
            }
            for i in range(6)
        ]
        generated = self.compiler.compile_video(
            "uploaded_portrait.png", "uploaded_speech.flac", segments, 42
        )
        self.assertEqual(set(generated), set(template))

        expected = copy.deepcopy(template)
        for graph in (expected, generated):
            graph["221"]["inputs"]["image"] = "<dynamic-image>"
            graph["238"]["inputs"]["audio"] = "<dynamic-audio>"
            graph["254"]["inputs"]["text"] = "<dynamic-prompts>"
            for node_id in ("260", "266", "294", "322", "349", "413"):
                graph[node_id]["inputs"]["index"] = "<dynamic-index>"
            for node_id in TRAIN_SAMPLER_IDS:
                graph[node_id]["inputs"]["seed"] = "<task-seed>"
        self.assertEqual(generated, expected)

    def test_eight_car_graph_extends_workflow_one_and_saves_only_final(self) -> None:
        segments = [
            {
                "index": i,
                "start": i * 4,
                "end": (i + 1) * 4,
                "spoken_text": f"第{i + 1}段",
                "action_prompt": f"她对着前方说话，第{i + 1}段自然动作。",
            }
            for i in range(8)
        ]
        generated = self.compiler.compile_video(
            "uploaded_portrait.png", "uploaded_speech.flac", segments, 42
        )
        template = json.loads(
            (ROOT / "infinitetalk-single-person-train_api.json").read_text(
                encoding="utf-8"
            )
        )

        executable_template_nodes = {
            node_id for node_id in template if node_id not in TRAIN_VIDEO_OUTPUT_IDS
        }
        self.assertTrue(executable_template_nodes.issubset(generated))
        self.assertEqual(
            sum(node["class_type"] == "WanInfiniteTalkToVideo" for node in generated.values()),
            8,
        )
        outputs = [
            node for node in generated.values() if node["class_type"] == "VHS_VideoCombine"
        ]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(sum(bool(node["inputs"]["save_output"]) for node in outputs), 1)
        self.assertNotIn("408", generated)
        self.assertNotIn("1009", generated)
        self.assertTrue(generated["1019"]["inputs"]["save_output"] is True)
        self.assertEqual(generated["1000"]["inputs"]["index"], 6)
        self.assertEqual(generated["1010"]["inputs"]["index"], 7)
        self._assert_references_exist(generated)

    def test_action_prompt_is_one_line_for_each_video_window(self) -> None:
        prompt = AIDirector._action_prompt(
            "第1段：她对着前方说话，手部自然摆动，\n轻微皱眉，露出可爱的表情"
        )
        self.assertEqual(prompt, "人物对着镜头说话，手部自然摆动，轻微皱眉，露出可爱的表情。")
        segments = [
            {
                "index": 0,
                "start": 0,
                "end": 4,
                "spoken_text": "测试",
                "action_prompt": prompt,
            }
        ]
        workflow = self.compiler.compile_video("portrait.png", "speech.flac", segments, 1)
        self.assertEqual(workflow["254"]["inputs"]["text"], prompt)
        self.assertNotIn("\n", workflow["254"]["inputs"]["text"])

    def test_approved_action_prompt_is_not_rewritten_before_infinite_talk(self) -> None:
        long_prompt = (
            "她对着前方说话，说到‘万亿级蓝海机遇’时身体微微后靠，同时右手展开"
            "掌心向上向外划小弧表示市场广阔，随后缓缓收回，眼神坚定充满信心。"
        )
        compact = self.compiler.compact_action_prompt(long_prompt)
        self.assertTrue(compact.startswith("人物对着镜头说话，"))
        self.assertIn("万亿级蓝海机遇", compact)
        self.assertIn("随后", compact)

    def test_duplicate_speaking_prefix_is_cleaned(self) -> None:
        prompt = self.compiler.compact_action_prompt(
            "她对着镜头说话。她对着镜头说话，手部自然摆动，表情俏皮。"
        )
        self.assertEqual(prompt, "人物对着镜头说话，手部自然摆动，表情俏皮。")

    def test_male_speaking_prefix_is_normalized_without_gender_assumption(self) -> None:
        prompt = self.compiler.compact_action_prompt(
            "他对着镜头说话，轻微点头，表情认真。"
        )
        self.assertEqual(prompt, "人物对着镜头说话，轻微点头，表情认真。")

    def test_comfy_upload_uses_regular_file_context(self) -> None:
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"name": "voice.wav", "subfolder": ""}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, files, data):
                self.uploaded = files["image"][1].read()
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "voice.wav"
            source.write_bytes(b"audio")
            fake_client = Client()
            with patch("app.comfyui.httpx.AsyncClient", return_value=fake_client):
                result = asyncio.run(
                    ComfyUIClient("http://127.0.0.1:8188").upload(source, "voice.wav")
                )
        self.assertEqual(result, "voice.wav")
        self.assertEqual(fake_client.uploaded, b"audio")

    def test_comfy_submit_returns_prompt_id_before_waiting(self) -> None:
        class Response:
            is_error = False

            def json(self):
                return {"prompt_id": "prompt-123"}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, json):
                self.url = url
                self.payload = json
                return Response()

        fake_client = Client()
        with patch("app.comfyui.httpx.AsyncClient", return_value=fake_client):
            prompt_id = asyncio.run(
                ComfyUIClient("http://127.0.0.1:8188").submit({"1": {}})
            )
        self.assertEqual(prompt_id, "prompt-123")
        self.assertEqual(fake_client.url, "http://127.0.0.1:8188/prompt")

    def test_comfy_execution_error_is_not_truncated(self) -> None:
        marker = "完整错误内容" * 1000
        detail = ComfyUIClient._format_execution_messages(
            [["execution_error", {"node_id": "335", "traceback": [marker]}]]
        )
        self.assertIn(marker, detail)
        self.assertIn('"node_id": "335"', detail)

    def test_comfy_failure_cleanup_unloads_models_and_frees_memory(self) -> None:
        class Response:
            def raise_for_status(self):
                return None

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, json):
                self.url = url
                self.payload = json
                return Response()

        fake_client = Client()
        with patch("app.comfyui.httpx.AsyncClient", return_value=fake_client):
            note = asyncio.run(
                ComfyUIClient("http://127.0.0.1:8188").release_memory_after_failure()
            )
        self.assertEqual(fake_client.url, "http://127.0.0.1:8188/free")
        self.assertEqual(
            fake_client.payload,
            {"unload_models": True, "free_memory": True},
        )
        self.assertIn("已请求卸载模型", note)

    def test_comfy_cancel_does_not_interrupt_a_different_running_prompt(self) -> None:
        class Response:
            def __init__(self, payload=None):
                self.payload = payload or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class Client:
            def __init__(self):
                self.posts = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url):
                return Response(
                    {
                        "queue_running": [[0, "other-prompt", {}, {}, []]],
                        "queue_pending": [],
                    }
                )

            async def post(self, url, json=None):
                self.posts.append((url, json))
                return Response()

        fake_client = Client()
        with patch("app.comfyui.httpx.AsyncClient", return_value=fake_client):
            asyncio.run(
                ComfyUIClient("http://127.0.0.1:8188").cancel("target-prompt")
            )
        self.assertEqual(fake_client.posts, [])

    def test_comfy_cancel_interrupts_only_when_target_is_running(self) -> None:
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "queue_running": [[0, "target-prompt", {}, {}, []]],
                    "queue_pending": [],
                }

        class Client:
            def __init__(self):
                self.posts = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url):
                return Response()

            async def post(self, url, json=None):
                self.posts.append((url, json))
                return Response()

        fake_client = Client()
        with patch("app.comfyui.httpx.AsyncClient", return_value=fake_client):
            asyncio.run(
                ComfyUIClient("http://127.0.0.1:8188").cancel("target-prompt")
            )
        self.assertEqual(
            fake_client.posts,
            [("http://127.0.0.1:8188/interrupt", None)],
        )

    def test_segment_count_uses_overlap(self) -> None:
        self.assertEqual(self.compiler.expected_segment_count(4.0), 1)
        self.assertEqual(self.compiler.expected_segment_count(4.5), 2)
        self.assertEqual(self.compiler.expected_segment_count(32.0), 8)

    def test_text_windows_match_count(self) -> None:
        windows = AIDirector._text_windows("第一句话。第二句话更长一些！最后一句。", 5)
        self.assertEqual(len(windows), 5)
        self.assertTrue(all(windows))

    def test_asr_forced_alignment_tracks_sentence_across_windows(self) -> None:
        script = "第一句话。第二句话很长！"
        payload = {
            "text": "第一句话第二句话很长",
            "words": [
                {"word": "第一句话", "start": 0.0, "end": 2.5},
                {"word": "第二句话很长", "start": 3.0, "end": 8.5},
            ],
        }
        recognized = SpeechAlignmentService._asr_chars(payload)
        chars, confidence = SpeechAlignmentService._force_align(script, recognized, 9.0)
        result = SpeechAlignmentService._build_result(
            script, 9.0, chars, "asr_forced", confidence, "test"
        )
        self.assertEqual(confidence, 1.0)
        self.assertEqual(len(result["windows"]), 3)
        self.assertTrue(result["windows"][0]["ends_mid_sentence"])
        self.assertTrue(result["windows"][1]["starts_mid_sentence"])
        self.assertTrue(result["windows"][1]["ends_mid_sentence"])
        self.assertTrue(result["windows"][2]["starts_mid_sentence"])
        self.assertEqual(
            result["windows"][1]["speech_events"][0]["full_sentence"], "第二句话很长！"
        )

    def test_estimated_alignment_also_preserves_cross_window_context(self) -> None:
        script = "这是一句会跨越多个视频窗口而且不能在边界突然改变动作的长句。"
        chars = SpeechAlignmentService._estimated_chars(script, 10.0)
        result = SpeechAlignmentService._build_result(
            script, 10.0, chars, "estimated", 0.4, "test"
        )
        self.assertTrue(result["windows"][0]["ends_mid_sentence"])
        self.assertTrue(result["windows"][1]["starts_mid_sentence"])
        self.assertTrue(result["windows"][1]["ends_mid_sentence"])

    def test_alignment_exposes_precise_sentence_timeline(self) -> None:
        script = "第一句话。第二句话！"
        payload = {
            "text": "第一句话第二句话",
            "words": [
                {"word": "第一句话", "start": 0.2, "end": 1.6},
                {"word": "第二句话", "start": 2.0, "end": 3.5},
            ],
        }
        recognized = SpeechAlignmentService._asr_chars(payload)
        chars, confidence = SpeechAlignmentService._force_align(
            script, recognized, 4.0
        )
        result = SpeechAlignmentService._build_result(
            script, 4.0, chars, "asr_forced", confidence, "test"
        )
        self.assertEqual(len(result["sentences"]), 2)
        self.assertEqual(result["sentences"][0]["start"], 0.2)
        self.assertEqual(result["sentences"][0]["end"], 1.6)
        self.assertEqual(result["sentences"][0]["pause_after"], 0.4)
        self.assertEqual(result["sentences"][1]["start"], 2.0)

    def test_alignment_diagnoses_long_pause_inside_sentence(self) -> None:
        script = "这是一句完整口播。"
        payload = {
            "text": "这是一句完整口播",
            "words": [
                {"word": "这是一句", "start": 0.0, "end": 1.0},
                {"word": "完整口播", "start": 2.1, "end": 3.1},
            ],
        }
        recognized = SpeechAlignmentService._asr_chars(payload)
        chars, confidence = SpeechAlignmentService._force_align(
            script, recognized, 3.2
        )
        result = SpeechAlignmentService._build_result(
            script, 3.2, chars, "asr_forced", confidence, "test"
        )
        quality = result["audio_quality"]
        self.assertTrue(quality["has_suspected_interruption"])
        self.assertTrue(
            any(issue["type"] == "long_pause_inside_sentence" for issue in quality["issues"])
        )

    def test_whisper_nested_word_timestamps_are_preferred(self) -> None:
        payload = {
            "text": "你好世界",
            "segments": [
                {
                    "text": "你好世界",
                    "start": 0.0,
                    "end": 2.0,
                    "words": [
                        {"word": "你好", "start": 0.0, "end": 0.8},
                        {"word": "世界", "start": 1.0, "end": 2.0},
                    ],
                }
            ],
        }
        chars = SpeechAlignmentService._asr_chars(payload)
        self.assertEqual("".join(item.char for item in chars), "你好世界")
        self.assertEqual(chars[1].end, 0.8)
        self.assertEqual(chars[2].start, 1.0)

    def test_whisper_cli_command_is_argument_list(self) -> None:
        configured = replace(
            settings,
            whisper_model="large-v3-turbo",
            whisper_language="Chinese",
            whisper_word_timestamps=True,
            whisper_model_dir="D:/models/whisper",
            whisper_device="cuda",
        )
        service = SpeechAlignmentService(configured)
        command = service._command(
            "whisper.exe", Path("C:/audio with spaces/speech.flac"), Path("C:/output dir")
        )
        self.assertIsInstance(command, list)
        self.assertIn(str(Path("C:/audio with spaces/speech.flac")), command)
        self.assertIn("--word_timestamps", command)
        self.assertIn("--model_dir", command)
        self.assertIn("--device", command)

    def test_missing_whisper_falls_back_without_server(self) -> None:
        configured = replace(settings, whisper_executable="Z:/missing/whisper.exe")
        service = SpeechAlignmentService(configured)
        result = asyncio.run(service.align(Path("unused.flac"), "测试口播。", 2.0))
        self.assertEqual(result["mode"], "estimated")
        self.assertIn("Whisper", result["note"])

    def test_separate_vision_provider_does_not_reuse_text_api_key(self) -> None:
        configured = replace(
            settings,
            ai_base_url="https://api.deepseek.com",
            ai_api_key="same-key",
            ai_vision_base_url="https://open.bigmodel.cn/api/paas/v4",
            ai_vision_api_key="same-key",
            ai_vision_model="glm-4.6v-flash",
        )
        self.assertEqual(configured.vision_api_key, "")
        self.assertFalse(configured.vision_enabled)

    def test_glm_vision_uses_raw_base64_and_thinking(self) -> None:
        configured = replace(
            settings,
            ai_vision_base_url="https://open.bigmodel.cn/api/paas/v4",
            ai_vision_api_key="zhipu-key",
            ai_vision_model="glm-4.6v-flash",
        )

        class CapturingDirector(AIDirector):
            captured_messages = None
            captured_options = None

            async def _chat(self, model, messages, **options):
                self.captured_messages = messages
                self.captured_options = options
                return {"script": "测试", "style": "自然", "emotion": {}, "image_analysis": COMPLETE_IMAGE_ANALYSIS}

        director = CapturingDirector(configured)
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "portrait.png"
            image_path.write_bytes(b"test-image")
            asyncio.run(
                director.analyze_and_write(
                    image_path, "测试", "口播", "观众", "自然"
                )
            )
        image_url = director.captured_messages[0]["content"][1]["image_url"]["url"]
        self.assertFalse(image_url.startswith("data:"))
        self.assertEqual(director.captured_options["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertEqual(director.captured_options["temperature"], 0.1)
        self.assertTrue(director.captured_options["json_mode"])

    def test_ai_json_parser_repairs_common_missing_commas(self) -> None:
        payload = AIDirector._parse_json(
            '```json\n{"image_analysis":{"pose":"正面坐姿"\n"light":"暖光",},\n'
            '"emotion":{"Happy":0.4}\n"style":"自然"}\n```'
        )
        self.assertEqual(payload["image_analysis"]["light"], "暖光")
        self.assertEqual(payload["emotion"]["Happy"], 0.4)
        self.assertEqual(payload["style"], "自然")

    def test_ai_json_parser_uses_friendly_format_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "分析结果格式不完整"):
            AIDirector._parse_json('{"image_analysis": "unfinished"')

    def test_ai_chat_repairs_format_without_resending_image(self) -> None:
        class FakeResponse:
            def __init__(self, content: str):
                self.content = content
                self.status_code = 200
                self.text = ""

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": self.content}}]}

        class FakeClient:
            def __init__(self):
                self.requests = []
                self.responses = [
                    FakeResponse('{"image_analysis": "unfinished"'),
                    FakeResponse('{"image_analysis":{"pose":"正面","light":"暖光"}}'),
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, url, *, headers, json):
                self.requests.append(json)
                return self.responses.pop(0)

        client = FakeClient()
        with patch("app.ai_director.httpx.AsyncClient", return_value=client):
            result = asyncio.run(
                AIDirector(settings)._chat(
                    "glm-test",
                    [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "analyze"},
                                {"type": "image_url", "image_url": {"url": "base64-image"}},
                            ],
                        }
                    ],
                    base_url="https://example.test/v1",
                    api_key="test-key",
                    json_mode=True,
                )
            )
        self.assertEqual(result["image_analysis"]["light"], "暖光")
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[0]["response_format"], {"type": "json_object"})
        self.assertEqual(client.requests[1]["temperature"], 0)
        self.assertEqual(client.requests[1]["messages"][0]["role"], "system")
        self.assertNotIn("image_url", str(client.requests[1]["messages"]))

    def test_separated_frontend_api_contract(self) -> None:
        from fastapi.testclient import TestClient
        from app.main import app

        schema = app.openapi()
        self.assertIn("/api/settings", schema["paths"])
        self.assertIn("get", schema["paths"]["/api/settings"])
        self.assertIn("patch", schema["paths"]["/api/settings"])
        self.assertIn("/api/projects/default", schema["paths"])
        self.assertIn("/api/projects/{project_id}/assets/{kind}", schema["paths"])
        self.assertIn("/api/projects/{project_id}/files/{kind}", schema["paths"])
        self.assertIn("/api/projects/{project_id}/enqueue", schema["paths"])
        self.assertIn("/api/tasks", schema["paths"])
        self.assertIn("/api/tasks/{task_id}/cancel", schema["paths"])
        self.assertIn("/api/tasks/{task_id}/retry", schema["paths"])
        self.assertIn("patch", schema["paths"]["/api/tasks/{task_id}"])
        self.assertIn("delete", schema["paths"]["/api/tasks/{task_id}"])
        response = TestClient(app).options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")

    def test_project_display_progress_does_not_reset_between_stages(self) -> None:
        from app.main import project_display_progress

        statuses = [
            "CREATED",
            "ANALYZING_IMAGE",
            "SCRIPT_READY",
            "GENERATING_AUDIO",
            "ALIGNING_SPEECH",
            "PLAN_READY",
            "GENERATING_VIDEO",
            "VIDEO_READY",
            "BURNING_SUBTITLES",
            "SUBTITLE_READY",
            "MIXING_BGM",
            "COMPLETED",
        ]
        values = [project_display_progress({"status": status}) for status in statuses]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 0)
        self.assertEqual(values[-1], 100)
        self.assertEqual(project_display_progress({"status": "SCRIPT_READY", "progress": 100}), 20)
        self.assertEqual(project_display_progress({"status": "GENERATING_VIDEO", "progress": 10}), 50)

    def test_public_project_exposes_overall_and_stage_progress(self) -> None:
        from app.main import public_project

        result = public_project({"status": "GENERATING_VIDEO", "progress": 10})
        self.assertEqual(result["progress"], 50)
        self.assertEqual(result["stage_progress"], 10)

    def test_task_display_uses_newer_project_state_after_historical_failure(self) -> None:
        from app.main import task_display_state

        task = {
            "status": "FAILED",
            "stage": "FAILED",
            "updated_at": "2026-08-06T03:19:51+00:00",
        }
        project = {
            "status": "GENERATING_VIDEO",
            "updated_at": "2026-08-06T03:28:00+00:00",
        }
        display = task_display_state(task, project)
        self.assertEqual(display["display_status"], "GENERATING_VIDEO")
        self.assertEqual(display["status_source"], "project_after_terminal_task")

    def test_task_display_keeps_current_unrecovered_failure(self) -> None:
        from app.main import task_display_state

        task = {
            "status": "FAILED",
            "stage": "FAILED",
            "updated_at": "2026-08-06T03:28:00+00:00",
        }
        project = {
            "status": "ERROR",
            "updated_at": "2026-08-06T03:28:00+00:00",
        }
        display = task_display_state(task, project)
        self.assertEqual(display["display_status"], "ERROR")
        self.assertEqual(display["status_source"], "queue")

    def test_original_script_locks_when_audio_generation_starts(self) -> None:
        from app.main import can_edit_task_script

        task = {"stage": "ANALYZING", "status": "RUNNING"}
        project = {"status": "ANALYZING_IMAGE", "audio_started": False}
        self.assertTrue(can_edit_task_script(task, project))
        project["audio_started"] = True
        self.assertFalse(can_edit_task_script(task, project))

    def test_completed_tasks_are_hidden_from_queue_api(self) -> None:
        from app import main

        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "jobs.db")
            completed = repository.enqueue_task("done", {"title": "done"})
            repository.update_task(
                completed["id"], status="COMPLETED", stage="COMPLETED"
            )
            failed = repository.enqueue_task("failed", {"title": "failed"})
            repository.update_task(
                failed["id"], status="FAILED", stage="FAILED"
            )
            with patch.object(main, "repository", repository):
                tasks = asyncio.run(main.list_production_tasks())
            self.assertEqual([task["id"] for task in tasks], [failed["id"]])

    def test_project_original_script_edit_updates_active_queue_snapshot(self) -> None:
        from app import main
        from app.schemas import ProjectPatch

        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "jobs.db")
            repository.create(
                {
                    "id": "job",
                    "title": "job",
                    "original_script": "旧稿",
                    "script": "旧导演稿",
                    "status": "SCRIPT_READY",
                    "audio_started": False,
                    "content_revision": 0,
                }
            )
            task = repository.enqueue_task(
                "job", {"title": "job", "original_script": "旧稿"}
            )
            with patch.object(main, "repository", repository):
                updated = asyncio.run(
                    main.patch_project(
                        "job", ProjectPatch(original_script="新口播稿")
                    )
                )
            self.assertEqual(updated["original_script"], "新口播稿")
            self.assertEqual(updated["script"], "")
            self.assertEqual(updated["status"], "QUEUE_WAITING")
            self.assertEqual(updated["content_revision"], 1)
            self.assertEqual(
                repository.get_task(task["id"])["snapshot"]["original_script"],
                "新口播稿",
            )

    def test_default_project_payload_can_declare_pending_asset_uploads(self) -> None:
        payload = ProjectCreate(
            original_script="测试口播",
            expect_image_upload=True,
            expect_voice_upload=True,
        )
        self.assertTrue(payload.expect_image_upload)
        self.assertTrue(payload.expect_voice_upload)

    def test_project_payload_does_not_accept_per_task_comfy_url(self) -> None:
        from app.schemas import ProjectPatch

        self.assertNotIn("comfy_url", ProjectCreate.model_fields)
        self.assertNotIn("comfy_url", ProjectPatch.model_fields)

    def test_repository_persists_global_comfy_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.db"
            first = ProjectRepository(database)
            self.assertEqual(
                first.get_setting("comfy_url", "http://fallback.test"),
                "http://fallback.test",
            )
            first.set_setting("comfy_url", "http://comfy.global")
            second = ProjectRepository(database)
            self.assertEqual(
                second.get_setting("comfy_url", "http://fallback.test"),
                "http://comfy.global",
            )

    def test_task_runner_uses_global_comfy_url(self) -> None:
        from app.orchestrator import TaskRunner

        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "jobs.db")
            repository.set_setting("comfy_url", "http://comfy.global")
            runner = TaskRunner(
                settings,
                repository,
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
            )
            self.assertEqual(runner._comfy_url(), "http://comfy.global")

    def test_repository_can_delete_one_project_without_touching_others(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "jobs.db")
            repository.create({"id": "first", "title": "first"})
            repository.create({"id": "second", "title": "second"})
            self.assertTrue(repository.delete("first"))
            self.assertIsNone(repository.get("first"))
            self.assertIsNotNone(repository.get("second"))
            self.assertFalse(repository.delete("missing"))

    def test_repository_deletes_only_terminal_task_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "jobs.db")
            queued = repository.enqueue_task("project", {"title": "project"})
            with self.assertRaisesRegex(ValueError, "只能删除"):
                repository.delete_task(queued["id"])
            repository.update_task(
                queued["id"], status="FAILED", stage="FAILED", error={"message": "test"}
            )
            deleted = repository.delete_task(queued["id"])
            self.assertEqual(deleted["status"], "FAILED")
            self.assertIsNone(repository.get_task(queued["id"]))

    def test_task_payload_edits_keep_queue_snapshot_in_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "jobs.db")
            queued = repository.enqueue_task(
                "project",
                {"title": "旧名称", "original_script": "旧口播稿"},
            )
            updated = repository.update_task_payload(
                queued["id"], title="新名称", original_script="新口播稿"
            )
            self.assertEqual(updated["project_title"], "新名称")
            self.assertEqual(updated["snapshot"]["title"], "新名称")
            self.assertEqual(updated["snapshot"]["original_script"], "新口播稿")

    def test_running_audio_task_can_be_cancelled_before_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "jobs.db")
            queued = repository.enqueue_task("project", {"title": "project"})
            repository.claim_next_task()
            repository.update_task(queued["id"], stage="GENERATING_AUDIO")
            cancelled = repository.cancel_task(queued["id"])
            self.assertEqual(cancelled["status"], "CANCELLED")
            deleted = repository.delete_task(queued["id"])
            self.assertEqual(deleted["stage"], "CANCELLED")

    def test_queue_cancels_active_execution_and_keeps_worker_alive(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = ProjectRepository(root / "jobs.db")
                input_dir = root / "job" / "input"
                input_dir.mkdir(parents=True)
                image = input_dir / "portrait.png"
                voice = input_dir / "voice.m4a"
                image.write_bytes(b"image")
                voice.write_bytes(b"voice")
                repository.create(
                    {
                        "id": "job",
                        "title": "job",
                        "original_script": "script",
                        "script": "script",
                        "image_analysis": {"character": "test"},
                        "image_path": str(image),
                        "voice_path": str(voice),
                        "auto_run": True,
                    }
                )
                started = asyncio.Event()
                remote_cancelled = False

                class BlockingRunner:
                    async def generate_audio(self, project_id: str) -> None:
                        started.set()
                        await asyncio.Event().wait()

                    async def generate_video(self, project_id: str) -> None:
                        raise AssertionError("cancelled task must not generate video")

                    async def cancel_project(self, project_id: str) -> None:
                        nonlocal remote_cancelled
                        remote_cancelled = True

                queue = ProductionQueue(
                    repository, BlockingRunner()  # type: ignore[arg-type]
                )
                await queue.start()
                try:
                    task = queue.enqueue("job")
                    await asyncio.wait_for(started.wait(), timeout=1)
                    cancelled = await queue.cancel(task["id"])
                    self.assertEqual(cancelled["status"], "CANCELLED")
                    self.assertTrue(remote_cancelled)
                    self.assertIsNotNone(queue._worker)
                    self.assertFalse(queue._worker.done())
                finally:
                    await queue.stop()

        asyncio.run(scenario())

    def test_second_tts_workflow_forces_fresh_image_analysis_before_audio(self) -> None:
        title = '4亿慢病患者，大多数人在"等病来"'
        script = (
            '跟你说个数据。国内确诊的慢病患者已经超过4亿，亚健康人群接近10亿。'
            '但绝大多数人仍在做一件事：等病来了才治。你知道"治未病"的成本，'
            '大概只有治病的十分之一吗？提前养和预防，比事后救火划算得多。'
            '可为什么大多数人还是选择等？因为"没病"的时候，人很难感到紧迫。'
            '这就是健康管理的第一个悖论：最需要行动的时候，恰恰是你感觉没事的时候。'
            '认同的扣个1。'
        )
        image_name = "陈彬.jpg"
        voice_name = (
            "奥运冠军郭晶晶谈健康，健康是一种责任 "
            "#健康观念 #健康生活 #调理 #健康养生 _.mp4.flac"
        )
        compiler = self.compiler

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                image = root / image_name
                voice = root / voice_name
                image.write_bytes(b"image")
                voice.write_bytes(b"voice")
                repository = ProjectRepository(root / "jobs.db")
                repository.create(
                    {
                        "id": "health-job",
                        "title": title,
                        "original_script": "旧口播稿",
                        "script": "旧导演稿",
                        "image_analysis": {"character_description": "旧图片分析"},
                        "analysis_required": False,
                        "image_path": str(image),
                        "voice_path": str(voice),
                        "emotion_voice_path": str(voice),
                        "tts_engine": "indextts2_voice_clone",
                        "auto_run": True,
                        "subtitle_enabled": False,
                        "video_title_enabled": False,
                    }
                )
                task = repository.enqueue_task(
                    "health-job", repository.get("health-job") or {}
                )
                repository.update(
                    "health-job",
                    original_script=script,
                    script="",
                    image_analysis=None,
                    analysis_required=True,
                )
                repository.update_task_payload(
                    task["id"], original_script=script
                )
                running = repository.claim_next_task()
                self.assertIsNotNone(running)
                calls: list[str] = []

                class FakeRunner:
                    async def analyze(self, project_id: str) -> None:
                        project = repository.get(project_id) or {}
                        calls.append("analyze")
                        self_outer.assertEqual(project["original_script"], script)
                        self_outer.assertEqual(Path(project["image_path"]), image)
                        repository.update(
                            project_id,
                            script=script,
                            image_analysis={
                                "character_description": "陈彬人物图片分析完成"
                            },
                            analysis_required=False,
                        )

                    async def generate_audio(self, project_id: str) -> None:
                        project = repository.get(project_id) or {}
                        calls.append("audio")
                        self_outer.assertEqual(
                            project["tts_engine"], "indextts2_voice_clone"
                        )
                        self_outer.assertEqual(Path(project["voice_path"]), voice)
                        self_outer.assertEqual(
                            Path(project["emotion_voice_path"]), voice
                        )
                        workflow = compiler.compile_tts(
                            project["script"],
                            voice.name,
                            {},
                            123,
                            project["tts_engine"],
                            voice.name,
                        )
                        self_outer.assertEqual(
                            workflow["13"]["inputs"]["audio"], voice.name
                        )
                        self_outer.assertEqual(
                            workflow["15"]["inputs"]["audio"], voice.name
                        )
                        output = root / "speech.flac"
                        output.write_bytes(b"audio")
                        repository.update(
                            project_id,
                            audio_path=str(output),
                            segments=[{"index": 0}],
                        )

                    async def generate_video(self, project_id: str) -> None:
                        calls.append("video")
                        output = root / "final.mp4"
                        output.write_bytes(b"video")
                        repository.update(project_id, video_path=str(output))

                queue = ProductionQueue(
                    repository, FakeRunner()  # type: ignore[arg-type]
                )
                await queue._execute(running or {})
                completed = repository.get_task(task["id"])
                self.assertEqual(calls, ["analyze", "audio", "video"])
                self.assertEqual(completed["status"], "COMPLETED")
                project = repository.get("health-job") or {}
                self.assertFalse(project["analysis_required"])
                self.assertIn("陈彬", project["image_analysis"]["character_description"])

        self_outer = self
        asyncio.run(scenario())

    def test_auto_task_enters_queue_before_assets_finish_uploading(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = ProjectRepository(root / "jobs.db")
                image = root / "job" / "input" / "portrait.png"
                voice = root / "job" / "input" / "voice.m4a"
                repository.create(
                    {
                        "id": "job",
                        "title": "job",
                        "original_script": "script",
                        "image_path": str(image),
                        "voice_path": str(voice),
                        "auto_run": True,
                        "assets_pending": True,
                    }
                )
                calls: list[str] = []

                class FakeRunner:
                    async def analyze(self, project_id: str) -> None:
                        calls.append("analyze")
                        repository.update(
                            project_id,
                            script="script",
                            image_analysis={"character": "test"},
                        )

                    async def generate_audio(self, project_id: str) -> None:
                        calls.append("audio")
                        output = root / "job" / "speech.flac"
                        output.write_bytes(b"audio")
                        repository.update(
                            project_id,
                            audio_path=str(output),
                            segments=[{"index": 0}],
                        )

                    async def generate_video(self, project_id: str) -> None:
                        calls.append("video")
                        output = root / "job" / "final.mp4"
                        output.write_bytes(b"video")
                        repository.update(project_id, video_path=str(output))

                queue = ProductionQueue(
                    repository, FakeRunner()  # type: ignore[arg-type]
                )
                await queue.start()
                try:
                    task = queue.enqueue("job")
                    for _ in range(100):
                        waiting = repository.get_task(task["id"])
                        if waiting and waiting["stage"] == "UPLOADING_ASSETS":
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(
                        repository.get_task(task["id"])["stage"],
                        "UPLOADING_ASSETS",
                    )
                    self.assertEqual(calls, [])
                    image.parent.mkdir(parents=True)
                    image.write_bytes(b"image")
                    voice.write_bytes(b"voice")
                    for _ in range(200):
                        completed = repository.get_task(task["id"])
                        if completed and completed["status"] == "COMPLETED":
                            break
                        await asyncio.sleep(0.01)
                finally:
                    await queue.stop()

                self.assertEqual(calls, ["analyze", "audio", "video"])
                self.assertFalse(repository.get("job")["assets_pending"])

        asyncio.run(scenario())

    def test_delete_task_removes_project_and_its_files(self) -> None:
        from app import main

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                data_dir = Path(directory)
                project_dir = data_dir / "jobs" / "job"
                project_dir.mkdir(parents=True)
                (project_dir / "result.txt").write_text("result", encoding="utf-8")
                repository = ProjectRepository(data_dir / "jobs.db")
                repository.create(
                    {
                        "id": "job",
                        "title": "job",
                        "project_dir": str(project_dir),
                    }
                )
                task = repository.enqueue_task("job", {"title": "job"})

                class FakeQueue:
                    async def cancel(self, task_id: str):
                        return repository.cancel_task(task_id)

                class FakeRunner:
                    tasks = {}

                configured = replace(settings, data_dir=data_dir)
                with (
                    patch.object(main, "repository", repository),
                    patch.object(main, "production_queue", FakeQueue()),
                    patch.object(main, "runner", FakeRunner()),
                    patch.object(main, "settings", configured),
                ):
                    result = await main.delete_production_task(task["id"])

                self.assertTrue(result["project_deleted"])
                self.assertTrue(result["files_removed"])
                self.assertFalse(project_dir.exists())
                self.assertIsNone(repository.get("job"))
                self.assertIsNone(repository.get_task(task["id"]))

        asyncio.run(scenario())

    def test_running_director_retry_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "jobs.db")
            queued = repository.enqueue_task("project", {"title": "project"})
            running = repository.claim_next_task()
            self.assertEqual(running["id"], queued["id"])
            repository.update_task(queued["id"], stage="ANALYSIS_RETRYING")
            cancelled = repository.cancel_task(queued["id"])
            self.assertEqual(cancelled["status"], "CANCELLED")

    def test_production_queue_executes_three_tasks_in_fifo_order(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = ProjectRepository(root / "jobs.db")
                calls: list[str] = []

                class FakeRunner:
                    async def analyze(self, project_id: str) -> None:
                        calls.append(f"{project_id}:analyze")
                        repository.update(
                            project_id,
                            script="locked script",
                            image_analysis={"character": "test"},
                        )

                    async def generate_audio(self, project_id: str) -> None:
                        calls.append(f"{project_id}:audio")
                        audio = root / project_id / "speech.flac"
                        audio.parent.mkdir(parents=True, exist_ok=True)
                        audio.write_bytes(b"audio")
                        repository.update(
                            project_id,
                            audio_path=str(audio),
                            segments=[{"index": 0}],
                        )

                    async def generate_video(self, project_id: str) -> None:
                        calls.append(f"{project_id}:video")
                        video = root / project_id / "final.mp4"
                        video.write_bytes(b"video")
                        repository.update(project_id, video_path=str(video))

                for project_id in ("one", "two", "three"):
                    input_dir = root / project_id / "input"
                    input_dir.mkdir(parents=True)
                    image = input_dir / "portrait.png"
                    voice = input_dir / "voice.m4a"
                    image.write_bytes(b"image")
                    voice.write_bytes(b"voice")
                    repository.create(
                        {
                            "id": project_id,
                            "title": project_id,
                            "original_script": "script",
                            "image_path": str(image),
                            "voice_path": str(voice),
                            "comfy_url": "http://comfy.test",
                        }
                    )

                queue = ProductionQueue(repository, FakeRunner())  # type: ignore[arg-type]
                await queue.start()
                try:
                    for project_id in ("one", "two", "three"):
                        queue.enqueue(project_id)
                    for _ in range(100):
                        if all(
                            task["status"] == "COMPLETED"
                            for task in repository.list_tasks()
                        ):
                            break
                        await asyncio.sleep(0.01)
                finally:
                    await queue.stop()

                self.assertEqual(
                    calls,
                    [
                        "one:analyze",
                        "one:audio",
                        "one:video",
                        "two:analyze",
                        "two:audio",
                        "two:video",
                        "three:analyze",
                        "three:audio",
                        "three:video",
                    ],
                )
                self.assertTrue(
                    all(
                        task["status"] == "COMPLETED"
                        for task in repository.list_tasks()
                    )
                )

        asyncio.run(scenario())

    def test_auto_queue_retries_director_until_success_then_runs_full_chain(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = ProjectRepository(root / "jobs.db")
                input_dir = root / "job" / "input"
                input_dir.mkdir(parents=True)
                image = input_dir / "portrait.png"
                voice = input_dir / "voice.m4a"
                image.write_bytes(b"image")
                voice.write_bytes(b"voice")
                repository.create(
                    {
                        "id": "job",
                        "title": "job",
                        "original_script": "script",
                        "image_path": str(image),
                        "voice_path": str(voice),
                        "comfy_url": "http://comfy.test",
                        "auto_run": True,
                    }
                )
                attempts = 0

                class FlakyDirectorRunner:
                    async def analyze(self, project_id: str) -> None:
                        nonlocal attempts
                        attempts += 1
                        if attempts < 3:
                            raise RuntimeError(f"temporary-{attempts}")
                        repository.update(
                            project_id,
                            script="locked script",
                            image_analysis={"character": "test"},
                        )

                    async def generate_audio(self, project_id: str) -> None:
                        audio = root / "job" / "speech.flac"
                        audio.write_bytes(b"audio")
                        repository.update(
                            project_id,
                            audio_path=str(audio),
                            segments=[{"index": 0}],
                        )

                    async def generate_video(self, project_id: str) -> None:
                        video = root / "job" / "final.mp4"
                        video.write_bytes(b"video")
                        repository.update(project_id, video_path=str(video))

                queue = ProductionQueue(
                    repository, FlakyDirectorRunner()  # type: ignore[arg-type]
                )
                queue.analysis_retry_seconds = 0.01
                await queue.start()
                try:
                    task = queue.enqueue("job")
                    for _ in range(100):
                        current = repository.get_task(task["id"])
                        if current and current["status"] == "COMPLETED":
                            break
                        await asyncio.sleep(0.01)
                finally:
                    await queue.stop()

                completed = repository.get_task(task["id"])
                self.assertEqual(attempts, 3)
                self.assertEqual(completed["status"], "COMPLETED")
                self.assertIsNone(completed["error"])

        asyncio.run(scenario())

    def test_director_keeps_user_script_as_the_only_content_source(self) -> None:
        configured = replace(
            settings,
            ai_vision_base_url="https://open.bigmodel.cn/api/paas/v4",
            ai_vision_api_key="vision-key",
            ai_vision_model="glm-4.6v-flash",
        )

        class RewritingModelDirector(AIDirector):
            async def _chat(self, model, messages, **options):
                return {
                    "script": "MODEL REWROTE THE SCRIPT",
                    "style": "natural",
                    "emotion": {},
                    "image_analysis": COMPLETE_IMAGE_ANALYSIS,
                }

        locked_script = "Company facts 5 years. Keep every word and number."
        image_path = Path(__file__)
        result = asyncio.run(
            RewritingModelDirector(configured).analyze_and_write(
                image_path, locked_script, "promotion", "audience", "professional"
            )
        )
        self.assertEqual(result["script"], locked_script)

    def test_action_director_receives_full_script_and_aligned_window(self) -> None:
        configured = replace(
            settings,
            ai_api_key="text-key",
            ai_text_model="text-model",
        )

        class CapturingDirector(AIDirector):
            prompt = ""

            async def _chat(self, model, messages, **options):
                self.prompt = messages[0]["content"]
                return {"segments": []}

        script = "Opening market opportunity. Explain company facts. Invite partners."
        windows = {
            "windows": [
                {
                    "index": 0,
                    "start": 0.0,
                    "end": 4.0,
                    "spoken_text": "Opening market opportunity.",
                    "sentence_context": "Opening market opportunity.",
                    "starts_mid_sentence": False,
                    "ends_mid_sentence": False,
                    "speech_events": [],
                }
            ]
        }
        director = CapturingDirector(configured)
        asyncio.run(director.plan_segments(script, 4.0, {}, "professional", windows))
        self.assertIn(script, director.prompt)
        self.assertIn("Opening market opportunity.", director.prompt)
        self.assertIn("semantic role", director.prompt)

    def test_alignment_builds_readable_subtitles_from_original_script(self) -> None:
        script = "百万亿健康管理蓝海市场，机遇就在眼前。"
        chars = SpeechAlignmentService._estimated_chars(script, 5.0)
        result = SpeechAlignmentService._build_result(
            script, 5.0, chars, "estimated", 0.45, "test"
        )
        cues = result["subtitle_cues"]
        self.assertEqual(result["subtitle_segmentation_version"], 2)
        self.assertGreaterEqual(len(cues), 2)
        self.assertEqual("".join(cue["text"] for cue in cues), script)
        self.assertTrue(all(cue["end"] > cue["start"] for cue in cues))
        self.assertTrue(
            all(left["end"] <= right["start"] for left, right in zip(cues, cues[1:]))
        )

    def test_custom_subtitle_length_keeps_character_timestamps(self) -> None:
        script = "这是连续逗号，，，后面继续说的一段较长口播文案。"
        chars = SpeechAlignmentService._estimated_chars(script, 5.0)
        result = SpeechAlignmentService._build_result(
            script, 5.0, chars, "estimated", 0.45, "test", 8
        )
        self.assertTrue(result["characters"])
        self.assertTrue(all(not cue["text"].startswith(("，", ",")) for cue in result["subtitle_cues"]))
        rebuilt = SpeechAlignmentService.subtitle_cues_from_timeline(
            script, result["characters"], 12
        )
        self.assertEqual("".join(cue["text"] for cue in rebuilt), script)

    def test_subtitle_balancing_does_not_leave_orphan_tail(self) -> None:
        script = "第一，只收钱、不给支持，交完钱就没人管；"
        chars = SpeechAlignmentService._estimated_chars(script, 4.0)
        cues = SpeechAlignmentService._subtitle_cues(
            script, chars, SpeechAlignmentService._sentence_ranges(script), 15
        )
        self.assertEqual([cue["text"] for cue in cues], [
            "第一，只收钱、不给支持，",
            "交完钱就没人管；",
        ])
        self.assertEqual("".join(cue["text"] for cue in cues), script)
        self.assertTrue(all(len(SpeechAlignmentService._normalized(cue["text"])) >= 5 for cue in cues))
        self.assertTrue(all(left["end"] <= right["start"] for left, right in zip(cues, cues[1:])))

    def test_subtitle_balancing_keeps_genuine_short_sentence(self) -> None:
        script = "对。第一，只收钱、不给支持，交完钱就没人管；"
        chars = SpeechAlignmentService._estimated_chars(script, 5.0)
        cues = SpeechAlignmentService._subtitle_cues(
            script, chars, SpeechAlignmentService._sentence_ranges(script), 15
        )
        self.assertEqual(cues[0]["text"], "对。")
        self.assertNotIn("管；", [cue["text"] for cue in cues])
        self.assertEqual("".join(cue["text"] for cue in cues), script)

    def test_legacy_subtitle_balancing_uses_the_same_orphan_rule(self) -> None:
        script = "第一，只收钱、不给支持，交完钱就没人管；"
        cues = SubtitleDocument.cues_from_sentences(
            [{"text": script, "start": 0.0, "end": 4.0}], 15
        )
        self.assertEqual([cue["text"] for cue in cues], [
            "第一，只收钱、不给支持，",
            "交完钱就没人管；",
        ])

    def test_short_video_subtitle_hides_pause_punctuation(self) -> None:
        self.assertEqual(
            subtitle_display_text("第一，只收钱、不给支持，交完钱就没人管；"),
            "第一 只收钱 不给支持 交完钱就没人管",
        )
        self.assertEqual(
            subtitle_display_text("增长3.5%，金额1,000元，时间12:30！真的吗？"),
            "增长3.5% 金额1,000元 时间12:30！真的吗？",
        )

    def test_subtitle_document_writes_srt_and_layered_ass(self) -> None:
        cues = [
            {"start": 0.0, "end": 1.25, "text": "第一句字幕"},
            {"start": 1.25, "end": 3.0, "text": "第二句字幕"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = SubtitleDocument.write_srt(root / "subtitle.srt", cues)
            ass = SubtitleDocument.write_ass(
                root / "subtitle.ass",
                cues,
                {
                    "subtitle_font_name": "Microsoft YaHei",
                    "subtitle_font_size": 60,
                    "subtitle_font_bold": True,
                    "subtitle_font_color": "#FFFFFF",
                    "subtitle_position": "custom",
                    "subtitle_custom_position": 72,
                    "subtitle_stroke_color": "#000000",
                    "subtitle_stroke_width": 2,
                    "subtitle_background_enabled": True,
                    "subtitle_background_color": "#112233",
                    "subtitle_background_opacity": 55,
                },
                1080,
                1920,
            )
            srt_text = srt.read_text(encoding="utf-8")
            ass_text = ass.read_text(encoding="utf-8-sig")
        self.assertIn("00:00:00,000 --> 00:00:01,250", srt_text)
        self.assertIn("Style: Text,Microsoft YaHei,60", ass_text)
        text_style = next(line for line in ass_text.splitlines() if line.startswith("Style: Text,"))
        self.assertEqual(text_style.split(",")[7], "-1")
        self.assertEqual(text_style.split(",")[5], "&H00000000")
        self.assertEqual(float(text_style.split(",")[16]), 2.0)
        self.assertIn("Style: Box,Microsoft YaHei,60", ass_text)
        self.assertIn("{\\pos(540,1382)}第一句字幕", ass_text)
        self.assertEqual(ass_text.count("Dialogue: 0,"), 2)
        self.assertEqual(ass_text.count("Dialogue: 1,"), 2)

    def test_project_thumbnail_is_cached_webp_with_fixed_dimensions(self) -> None:
        from PIL import Image
        from app.main import build_project_thumbnail, public_project_summary

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "portrait.png"
            destination = root / "cache" / "portrait.webp"
            Image.new("RGB", (900, 1200), (30, 80, 140)).save(source)
            summary = public_project_summary({"id": "demo", "image_path": str(source)})
            self.assertTrue(summary["has_image"])
            self.assertTrue(summary["thumbnail_version"].startswith("portrait-v2-"))
            result = build_project_thumbnail(source, destination)
            first_mtime = result.stat().st_mtime_ns
            cached = build_project_thumbnail(source, destination)
            self.assertEqual(cached.stat().st_mtime_ns, first_mtime)
            with Image.open(cached) as thumbnail:
                self.assertEqual(thumbnail.format, "WEBP")
                self.assertEqual(thumbnail.size, (270, 480))

    def test_completed_video_locks_only_production_stages(self) -> None:
        from app.main import production_stage_locked

        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "finished.mp4"
            video.write_bytes(b"video")
            project = {"raw_video_path": str(video)}
            self.assertTrue(production_stage_locked(project, "analyze"))
            self.assertTrue(production_stage_locked(project, "audio"))
            self.assertTrue(production_stage_locked(project, "video"))
            self.assertFalse(production_stage_locked(project, "subtitle"))
            self.assertFalse(production_stage_locked(project, "bgm"))

    def test_video_title_supports_three_line_colors_for_full_video_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ass = SubtitleDocument.write_ass(
                Path(directory) / "title.ass",
                [{"start": 0.0, "end": 2.0, "text": "\u6d4b\u8bd5\u5b57\u5e55"}],
                {
                    "video_title_enabled": True,
                    "video_title": "\u7b2c\u4e00\u884c\u6807\u9898\n\u7b2c\u4e8c\u884c\u6807\u9898\n\u7b2c\u4e09\u884c\u6807\u9898\n\u7b2c\u56db\u884c\u4e0d\u4f1a\u663e\u793a",
                    "video_title_font_name": "Microsoft YaHei",
                    "video_title_font_size": 88,
                    "video_title_primary_color": "#FFFFFF",
                    "video_title_secondary_color": "#FFD84D",
                    "video_title_tertiary_color": "#7DE3FF",
                    "video_title_position": 10,
                    "video_title_stroke_color": "#000000",
                    "video_title_stroke_width": 4,
                },
                1080,
                1920,
                video_duration=16.64,
            )
            content = ass.read_text(encoding="utf-8-sig")
        self.assertIn("Style: Title,Microsoft YaHei,88,&H00FFFFFF", content)
        self.assertNotIn("TitleAccent", content)
        self.assertEqual(content.count("Dialogue: 2,"), 3)
        self.assertIn("{\\pos(540,192)\\1c&H00FFFFFF}\u7b2c\u4e00\u884c\u6807\u9898", content)
        self.assertIn("{\\pos(540,298)\\1c&H004DD8FF}\u7b2c\u4e8c\u884c\u6807\u9898", content)
        self.assertIn("{\\pos(540,404)\\1c&H00FFE37D}\u7b2c\u4e09\u884c\u6807\u9898", content)
        self.assertNotIn("\u7b2c\u56db\u884c\u4e0d\u4f1a\u663e\u793a", content)

    def test_video_title_suggestion_uses_first_two_script_clauses(self) -> None:
        from app.main import suggest_video_title

        self.assertEqual(
            suggest_video_title("百万亿健康管理蓝海市场，机遇就在眼前。后续正文。"),
            "百万亿健康管理蓝海市场\n机遇就在眼前",
        )

    def test_video_segment_node_map_covers_extended_train(self) -> None:
        segments = [
            {
                "index": index,
                "start": index * 4,
                "end": (index + 1) * 4,
                "spoken_text": f"第{index + 1}段",
                "action_prompt": "人物对着镜头说话。",
            }
            for index in range(10)
        ]
        workflow = self.compiler.compile_video("portrait.png", "speech.flac", segments, 42)
        mapping = self.compiler.video_segment_node_map(workflow, len(segments))
        self.assertEqual(sorted(mapping.values()), list(range(1, 11)))
        self.assertEqual(mapping["162"], 1)
        self.assertEqual(mapping["401"], 6)
        self.assertEqual(mapping["1035"], 10)
        self.assertEqual(
            {workflow[node_id]["inputs"]["seed"] for node_id in mapping},
            {42},
        )

    def test_repository_publishes_project_and_task_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ProjectRepository(Path(directory) / "events.db")
            events = []
            repository.add_listener(lambda entity, payload: events.append((entity, payload)))
            project = repository.create(
                {"id": "event-project", "title": "事件测试", "status": "CREATED", "progress": 0}
            )
            repository.update(project["id"], status="GENERATING_VIDEO", progress=20)
            task = repository.enqueue_task(project["id"], repository.get(project["id"]) or project)
            repository.update_task(task["id"], status="RUNNING", stage="GENERATING_VIDEO")
        self.assertTrue(any(entity == "project" for entity, _ in events))
        self.assertTrue(any(entity == "task" for entity, _ in events))
        self.assertTrue(all("original_script" not in payload for _, payload in events))

    def test_realtime_project_event_expands_to_public_summary(self) -> None:
        from app import main

        project = {
            "id": "realtime-project",
            "title": "实时项目",
            "status": "GENERATING_VIDEO",
            "progress": 50,
            "updated_at": "2026-01-01T00:00:00",
        }
        with patch.object(main.repository, "get", return_value=project):
            event = main.public_realtime_event(
                {"id": 7, "entity": "project", "payload": {"id": project["id"]}}
            )
        self.assertEqual(event["id"], 7)
        self.assertEqual(event["payload"]["title"], "实时项目")
        self.assertEqual(event["payload"]["status"], "GENERATING_VIDEO")
        self.assertIn("progress", event["payload"])

    def test_event_broker_delivers_compact_sse_event(self) -> None:
        from app.event_stream import EventBroker

        async def scenario() -> None:
            broker = EventBroker()
            broker.bind_loop(asyncio.get_running_loop())
            async with broker.subscribe() as queue:
                broker.publish("project", {"id": "p1", "status": "GENERATING_VIDEO"})
                event = await asyncio.wait_for(queue.get(), timeout=1)
                self.assertEqual(event["entity"], "project")
                self.assertEqual(event["payload"]["id"], "p1")
                encoded = broker.encode_sse(event)
                self.assertIn("data:", encoded)
                self.assertNotIn("original_script", encoded)

        asyncio.run(scenario())

    def test_dynamic_video_progress_uses_train_segment_position(self) -> None:
        from app.main import project_display_progress, public_project_summary

        project = {
            "id": "p1",
            "title": "测试",
            "status": "GENERATING_VIDEO",
            "progress": 28,
            "video_segment_current": 3,
            "video_segment_total": 11,
            "video_segment_progress": 0.5,
            "original_script": "不应该进入摘要" * 100,
        }
        overall = project_display_progress(project)
        self.assertGreater(overall, 40)
        self.assertLess(overall, 87)
        summary = public_project_summary(project)
        self.assertEqual(summary["video_segment_current"], 3)
        self.assertNotIn("original_script", summary)

    def test_comfyui_websocket_url_preserves_reverse_proxy_path(self) -> None:
        client = ComfyUIClient("https://example.test/comfy")
        self.assertEqual(
            client._websocket_url("client-1"),
            "wss://example.test/comfy/ws?clientId=client-1",
        )

    def _assert_references_exist(self, workflow: dict) -> None:
        for node_id, node in workflow.items():
            for value in node.get("inputs", {}).values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    self.assertIn(value[0], workflow, f"{node_id} 引用了缺失节点 {value[0]}")


if __name__ == "__main__":
    unittest.main()
