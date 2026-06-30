package com.guardian.agent.telemetry

import org.junit.Assert.assertEquals
import org.junit.Test

class AgentTelemetryRouterTest {

    @Test
    fun classifyBrowserUrl_detectsYoutubeWatchPages() {
        assertEquals(
            AgentTelemetryRouter.BrowserUrlKind.YoutubeVideo,
            AgentTelemetryRouter.classifyBrowserUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        )
        assertEquals(
            AgentTelemetryRouter.BrowserUrlKind.YoutubeVideo,
            AgentTelemetryRouter.classifyBrowserUrl("https://youtube.com/shorts/abc123"),
        )
    }

    @Test
    fun classifyBrowserUrl_detectsTiktokVideoPages() {
        assertEquals(
            AgentTelemetryRouter.BrowserUrlKind.TiktokVideo,
            AgentTelemetryRouter.classifyBrowserUrl("https://www.tiktok.com/@creator/video/1234567890"),
        )
    }

    @Test
    fun classifyBrowserUrl_treatsGeneralPagesAsWebHistory() {
        assertEquals(
            AgentTelemetryRouter.BrowserUrlKind.GeneralWeb,
            AgentTelemetryRouter.classifyBrowserUrl("https://www.wikipedia.org"),
        )
        assertEquals(
            AgentTelemetryRouter.BrowserUrlKind.GeneralWeb,
            AgentTelemetryRouter.classifyBrowserUrl("https://www.youtube.com/feed/trending"),
        )
    }
}
