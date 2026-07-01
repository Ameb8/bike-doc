package com.bikedoc.android.sessions.chat

import com.bikedoc.android.BuildConfig
import com.bikedoc.android.sessions.models.SseEvent
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import timber.log.Timber
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SseEventSource
    @Inject
    constructor(
        private val okHttpClient: OkHttpClient,
        private val json: Json,
    ) {
        fun connect(
            sessionId: String,
            after: String? = null,
        ): Flow<SseEvent> =
            callbackFlow {
                val source =
                    EventSources.createFactory(okHttpClient).newEventSource(
                        buildRequest(sessionId, after),
                        object : EventSourceListener() {
                            override fun onEvent(
                                eventSource: EventSource,
                                id: String?,
                                type: String?,
                                data: String,
                            ) {
                                try {
                                    trySend(SseEvent.parse(type, id, data, json))
                                } catch (exception: SerializationException) {
                                    Timber.e(exception, "Failed to parse SSE event type=%s id=%s", type, id)
                                }
                            }

                            override fun onClosed(eventSource: EventSource) {
                                channel.close()
                            }

                            override fun onFailure(
                                eventSource: EventSource,
                                throwable: Throwable?,
                                response: Response?,
                            ) {
                                close(throwable ?: IOException("SSE failure"))
                            }
                        },
                    )

                awaitClose { source.cancel() }
            }

        private fun buildRequest(
            sessionId: String,
            after: String?,
        ): Request {
            val urlBuilder =
                BuildConfig.API_BASE_URL.toHttpUrl()
                    .newBuilder()
                    .addPathSegments("v1/repair-sessions/$sessionId/events")
            if (after != null) {
                urlBuilder.addQueryParameter("after", after)
            }

            val requestBuilder = Request.Builder().url(urlBuilder.build())
            if (after != null) {
                requestBuilder.header("Last-Event-ID", after)
            }
            return requestBuilder.build()
        }
    }
