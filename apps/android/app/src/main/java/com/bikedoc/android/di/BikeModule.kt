package com.bikedoc.android.di

import com.bikedoc.android.api.BikeRepository
import com.bikedoc.android.api.DefaultBikeRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class BikeModule {
    @Binds
    abstract fun bindBikeRepository(repository: DefaultBikeRepository): BikeRepository
}
