package com.bikedoc.android.di

import com.bikedoc.android.api.AuthInterceptor
import com.bikedoc.android.api.BikeDocApiClient
import com.bikedoc.android.api.BikeDocApiService
import com.bikedoc.android.auth.AuthProvider
import com.bikedoc.android.auth.FirebaseAuthProvider
import com.bikedoc.android.core.IoDispatcher
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class AuthModule {
    @Binds
    @Singleton
    abstract fun bindAuthProvider(provider: FirebaseAuthProvider): AuthProvider
}

@Module
@InstallIn(SingletonComponent::class)
object CoreModule {
    @Provides
    @IoDispatcher
    fun provideIoDispatcher(): CoroutineDispatcher = Dispatchers.IO

    @Provides
    @Singleton
    fun provideJson(): Json =
        Json {
            ignoreUnknownKeys = true
        }

    @Provides
    @Singleton
    fun provideOkHttpClient(authInterceptor: AuthInterceptor): OkHttpClient =
        OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .build()

    @Provides
    @Singleton
    fun provideBikeDocApiService(apiClient: BikeDocApiClient): BikeDocApiService = apiClient.service
}
