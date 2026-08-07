from __future__ import annotations

import unittest
import asyncio
import copy
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.ai_director import AIDirector
from app.alignment import SpeechAlignmentService
from app.audio import audio_duration
from app.config import settings
from app.comfyui import ComfyUIClient
from app.production_queue import ProductionQueue
from app.repository import ProjectRepository
from app.schemas import ProjectCreate
from app.workflows import TRAIN_VIDEO_OUTPUT_IDS, WorkflowCompiler


ROOT = Path(__file__).resolve().parent.parent


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
        self.assertTrue(clone.auto_run)
        self.assertTrue(clone.expect_emotion_voice_upload)

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
        self.assertEqual(prompt, "她对着前方说话，手部自然摆动，轻微皱眉，露出可爱的表情。")
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

    def test_long_director_prompt_is_compacted_for_infinite_talk(self) -> None:
        long_prompt = (
            "她对着前方说话，说到‘万亿级蓝海机遇’时身体微微后靠，同时右手展开"
            "掌心向上向外划小弧表示市场广阔，随后缓缓收回，眼神坚定充满信心。"
        )
        compact = self.compiler.compact_action_prompt(long_prompt)
        self.assertTrue(compact.startswith("她对着前方说话，"))
        self.assertLessEqual(len(compact), 36)
        self.assertNotIn("万亿级蓝海机遇", compact)
        self.assertNotIn("随后", compact)

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
                return {"script": "测试", "style": "自然", "emotion": {}, "image_analysis": {}}

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

    def test_separated_frontend_api_contract(self) -> None:
        from fastapi.testclient import TestClient
        from app.main import app

        schema = app.openapi()
        self.assertIn("/api/settings", schema["paths"])
        self.assertIn("get", schema["paths"]["/api/settings"])
        self.assertIn("patch", schema["paths"]["/api/settings"])
        self.assertIn("/api/projects/default", schema["paths"])
        self.assertIn("/api/projects/{project_id}/assets/{kind}", schema["paths"])
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
                    "image_analysis": {},
                }

        locked_script = "Company facts 5 years. Keep every word and number."
        image_path = next((ROOT / "material").glob("*.png"))
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

    def _assert_references_exist(self, workflow: dict) -> None:
        for node_id, node in workflow.items():
            for value in node.get("inputs", {}).values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    self.assertIn(value[0], workflow, f"{node_id} 引用了缺失节点 {value[0]}")


if __name__ == "__main__":
    unittest.main()
