package com.bikedoc.android.di

import com.bikedoc.android.bikes.BikeListRepository
import com.bikedoc.android.bikes.DefaultBikeListRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class BikeListModule {
    @Binds
    abstract fun bindBikeListRepository(repository: DefaultBikeListRepository): BikeListRepository
}
