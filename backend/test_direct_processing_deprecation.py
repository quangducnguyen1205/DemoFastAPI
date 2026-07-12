import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.routers import videos


class DirectProcessingRouteDeprecationTest(unittest.IsolatedAsyncioTestCase):
    def test_direct_upload_route_keeps_the_same_path_and_is_marked_deprecated(self) -> None:
        app = FastAPI()
        app.include_router(videos.router, prefix="/videos")

        route = next(
            route
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path == "/videos/upload"
            and "POST" in route.methods
        )

        self.assertTrue(route.deprecated)

    async def test_handler_remains_callable_and_emits_only_the_safe_warning(self) -> None:
        file = MagicMock()
        file.content_type = "video/mp4"
        file.filename = "synthetic-video.mp4"
        expected_response = {"task_id": "synthetic-task", "status": "processing", "video_id": 1}

        with (
            patch.object(videos, "run_in_threadpool", new=AsyncMock(return_value=expected_response)) as threadpool,
            self.assertLogs(videos.logger.name, level="WARNING") as captured,
        ):
            response = await videos.upload_video(
                file=file,
                title="Synthetic title",
                owner_id=None,
                db=MagicMock(),
            )

        self.assertEqual(response, expected_response)
        threadpool.assert_awaited_once()
        self.assertEqual(
            captured.output,
            [f"WARNING:{videos.logger.name}:{videos.DIRECT_PROCESSING_DEPRECATION_WARNING}"],
        )
        self.assertNotIn("Synthetic title", captured.output[0])
        self.assertNotIn("synthetic-video.mp4", captured.output[0])


if __name__ == "__main__":
    unittest.main()
