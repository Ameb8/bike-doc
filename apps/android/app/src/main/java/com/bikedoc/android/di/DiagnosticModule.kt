package com.bikedoc.android.di

import com.bikedoc.android.api.ArtifactRepository
import com.bikedoc.android.api.DefaultArtifactRepository
import com.bikedoc.android.api.DefaultReportRepository
import com.bikedoc.android.api.DefaultSessionRepository
import com.bikedoc.android.api.ReportRepository
import com.bikedoc.android.api.SessionRepository
import com.bikedoc.android.sessions.chat.ContentResolverDiagnosticPhotoPreparer
import com.bikedoc.android.sessions.chat.DiagnosticPhotoPreparer
import com.bikedoc.android.sessions.chat.OkHttpSseEventSource
import com.bikedoc.android.sessions.chat.SseEventSource
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class DiagnosticModule {
    @Binds
    abstract fun bindSessionRepository(repository: DefaultSessionRepository): SessionRepository

    @Binds
    abstract fun bindArtifactRepository(repository: DefaultArtifactRepository): ArtifactRepository

    @Binds
    abstract fun bindReportRepository(repository: DefaultReportRepository): ReportRepository

    @Binds
    abstract fun bindDiagnosticPhotoPreparer(preparer: ContentResolverDiagnosticPhotoPreparer): DiagnosticPhotoPreparer

    @Binds
    abstract fun bindSseEventSource(eventSource: OkHttpSseEventSource): SseEventSource
}
