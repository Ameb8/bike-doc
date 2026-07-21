package com.bikedoc.android.bikes

import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.BikeRepository
import javax.inject.Inject

data class BikeListItem(
    val id: String,
    val name: String,
    val makeModelYear: String,
    val specificationSummary: String,
    val hasRepairSessions: Boolean,
)

interface BikeListRepository {
    suspend fun getBikes(): ApiResult<List<BikeListItem>>

    suspend fun deleteBike(bikeId: String): BikeDeleteResult
}

sealed interface BikeDeleteResult {
    data object Success : BikeDeleteResult

    data object RepairHistoryConflict : BikeDeleteResult

    data class Error(val message: String) : BikeDeleteResult
}

class DefaultBikeListRepository
    @Inject
    constructor(
        private val bikeRepository: BikeRepository,
    ) : BikeListRepository {
        override suspend fun getBikes(): ApiResult<List<BikeListItem>> =
            when (val result = bikeRepository.getBikes()) {
                is ApiResult.Success ->
                    ApiResult.Success(
                        result.data.map { bike ->
                            BikeListItem(
                                id = bike.id,
                                name = bike.displayName,
                                makeModelYear =
                                    buildMakeModelYear(
                                        bike.identity.make,
                                        bike.identity.model,
                                        bike.identity.modelYear,
                                    ),
                                specificationSummary =
                                    buildSpecificationSummary(bike),
                                hasRepairSessions = bike.hasRepairSessions,
                            )
                        },
                    )
                is ApiResult.Error -> result
                ApiResult.Loading -> ApiResult.Loading
            }

        override suspend fun deleteBike(bikeId: String): BikeDeleteResult =
            when (
                val result =
                    bikeRepository.deleteBike(bikeId)
            ) {
                is ApiResult.Success -> BikeDeleteResult.Success
                is ApiResult.Error ->
                    if (result.code == 409) {
                        BikeDeleteResult.RepairHistoryConflict
                    } else {
                        BikeDeleteResult.Error(result.message)
                    }
                ApiResult.Loading -> BikeDeleteResult.Error("Something went wrong. Try again.")
            }

        private fun buildMakeModelYear(
            make: String?,
            model: String?,
            modelYear: Int?,
        ): String {
            val parts =
                listOfNotNull(
                    make?.takeIf { it.isNotBlank() },
                    model?.takeIf { it.isNotBlank() },
                    modelYear?.toString(),
                )
            return parts.joinToString(separator = " ").ifBlank { "Details coming soon" }
        }

        private fun buildSpecificationSummary(bike: BikeProfile): String {
            val parts =
                listOfNotNull(
                    bike.drivetrain.architecture?.replace('_', ' ')?.capitalizeWords(),
                    brakeSummary(bike.brakes),
                )
            return parts.joinToString(separator = " • ").ifBlank { "Specifications coming soon" }
        }

        private fun brakeSummary(brakes: BikeBrakes): String? {
            val front = brakes.front.mechanism
            val rear = brakes.rear.mechanism
            return when {
                front == null || rear == null -> null
                front == rear -> front.replace('_', ' ').capitalizeWords()
                else -> "Mixed brakes"
            }
        }

        private fun String.capitalizeWords() = split(' ').joinToString(" ") { it.replaceFirstChar(Char::uppercaseChar) }
    }
