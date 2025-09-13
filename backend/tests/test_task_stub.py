from unittest.mock import patch
from app.tasks.video_tasks import process_video_task


def test_process_video_task_stub(tmp_path):
    fake_video = tmp_path / 'fake.mp4'
    fake_video.write_text('not a real video')

    patches = [
        patch('app.tasks.video_tasks.extract_audio_to_wav', return_value=str(fake_video)),
        patch('app.tasks.video_tasks.transcribe_audio_with_whisper', return_value='hello world segment one two'),
        patch('app.tasks.video_tasks.segment_text', return_value=['hello world', 'segment two']),
        patch('app.tasks.video_tasks.persist_transcript_segments'),
        patch('app.tasks.video_tasks.embed_and_update_faiss'),
    ]
    for p in patches:
        p.start()
    try:
        result = process_video_task(video_id=999, abs_video_path=str(fake_video))
        assert result['status'] == 'failed'
    finally:
        for p in patches:
            p.stop()
