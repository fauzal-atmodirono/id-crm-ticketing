import httpx
import respx

from app.services.media import fetch_attachment_bytes


@respx.mock
async def test_fetch_attachment_bytes_success():
    respx.get("https://cdn.example.com/voice.ogg").mock(
        return_value=httpx.Response(200, content=b"fake-audio-bytes", headers={"Content-Type": "audio/ogg"})
    )
    result = await fetch_attachment_bytes("https://cdn.example.com/voice.ogg")
    assert result == (b"fake-audio-bytes", "audio/ogg")


@respx.mock
async def test_fetch_attachment_bytes_404_returns_none():
    respx.get("https://cdn.example.com/gone.jpg").mock(return_value=httpx.Response(404))
    assert await fetch_attachment_bytes("https://cdn.example.com/gone.jpg") is None


@respx.mock
async def test_fetch_attachment_bytes_network_error_returns_none():
    respx.get("https://cdn.example.com/timeout.jpg").mock(side_effect=httpx.ConnectError("down"))
    assert await fetch_attachment_bytes("https://cdn.example.com/timeout.jpg") is None


@respx.mock
async def test_fetch_attachment_bytes_missing_content_type_falls_back_to_octet_stream():
    respx.get("https://cdn.example.com/noheader.bin").mock(
        return_value=httpx.Response(200, content=b"data")
    )
    result = await fetch_attachment_bytes("https://cdn.example.com/noheader.bin")
    assert result is not None
    data, mime = result
    assert data == b"data"
    assert mime  # some non-empty fallback mime type, exact value not asserted


@respx.mock
async def test_fetch_attachment_bytes_missing_content_type_no_hint_falls_back_to_octet_stream():
    respx.get("https://cdn.example.com/noheader2.bin").mock(
        return_value=httpx.Response(200, content=b"data")
    )
    result = await fetch_attachment_bytes("https://cdn.example.com/noheader2.bin")
    assert result == (b"data", "application/octet-stream")


@respx.mock
async def test_fetch_attachment_bytes_missing_content_type_audio_hint_falls_back_to_ogg():
    respx.get("https://cdn.example.com/voice-noheader.bin").mock(
        return_value=httpx.Response(200, content=b"fake-audio-bytes")
    )
    result = await fetch_attachment_bytes(
        "https://cdn.example.com/voice-noheader.bin", file_type_hint="audio"
    )
    assert result == (b"fake-audio-bytes", "audio/ogg")


@respx.mock
async def test_fetch_attachment_bytes_missing_content_type_image_hint_falls_back_to_jpeg():
    respx.get("https://cdn.example.com/photo-noheader.bin").mock(
        return_value=httpx.Response(200, content=b"fake-image-bytes")
    )
    result = await fetch_attachment_bytes(
        "https://cdn.example.com/photo-noheader.bin", file_type_hint="image"
    )
    assert result == (b"fake-image-bytes", "image/jpeg")


@respx.mock
async def test_fetch_attachment_bytes_generic_content_type_falls_back_to_hint():
    respx.get("https://cdn.example.com/generic.bin").mock(
        return_value=httpx.Response(
            200, content=b"fake-audio-bytes", headers={"Content-Type": "application/octet-stream"}
        )
    )
    result = await fetch_attachment_bytes(
        "https://cdn.example.com/generic.bin", file_type_hint="audio"
    )
    assert result == (b"fake-audio-bytes", "audio/ogg")


@respx.mock
async def test_fetch_attachment_bytes_real_content_type_wins_over_hint():
    respx.get("https://cdn.example.com/photo.png").mock(
        return_value=httpx.Response(200, content=b"png-bytes", headers={"Content-Type": "image/png"})
    )
    result = await fetch_attachment_bytes("https://cdn.example.com/photo.png", file_type_hint="audio")
    assert result == (b"png-bytes", "image/png")
