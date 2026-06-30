package com.bikedoc.android.di

import com.bikedoc.android.api.DefaultSessionRepository
import com.bikedoc.android.api.SessionRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class DiagnosticModule {
    @Binds
    abstract fun bindSessionRepository(repository: DefaultSessionRepository): SessionRepository
}
