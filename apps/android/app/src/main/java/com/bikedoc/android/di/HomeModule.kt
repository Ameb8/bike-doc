package com.bikedoc.android.di

import com.bikedoc.android.home.DefaultHomeRepository
import com.bikedoc.android.home.HomeRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class HomeModule {
    @Binds
    abstract fun bindHomeRepository(repository: DefaultHomeRepository): HomeRepository
}
