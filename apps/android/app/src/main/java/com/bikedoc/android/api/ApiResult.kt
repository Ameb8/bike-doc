package com.bikedoc.android.api

import kotlinx.serialization.SerializationException
import retrofit2.HttpException
import timber.log.Timber
import java.io.IOException

sealed class ApiResult<out T> {
    data class Success<T>(val data: T) : ApiResult<T>()

    data class Error(val code: Int?, val message: String) : ApiResult<Nothing>()

    data object Loading : ApiResult<Nothing>()
}

suspend fun <T> safeApiCall(call: suspend () -> T): ApiResult<T> =
    try {
        ApiResult.Success(call())
    } catch (exception: HttpException) {
        Timber.e(exception, "HTTP API call failed with status %s", exception.code())
        ApiResult.Error(exception.code(), mapHttpError(exception))
    } catch (exception: IOException) {
        Timber.e(exception, "Network API call failed")
        ApiResult.Error(null, "Network error. Check your connection.")
    } catch (exception: SerializationException) {
        Timber.e(exception, "API response decoding failed")
        ApiResult.Error(null, "Unexpected server response. Please try again.")
    }

private fun mapHttpError(exception: HttpException): String =
    when (exception.code()) {
        401 -> "Session expired. Please sign in again."
        403 -> "You don't have permission to do that."
        404 -> "Not found."
        in 500..599 -> "Something went wrong. Try again."
        else -> exception.message()
    }
