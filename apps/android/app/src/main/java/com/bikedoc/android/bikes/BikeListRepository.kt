package com.bikedoc.android.bikes

import com.bikedoc.android.api.ApiResult
import com.bikedoc.android.api.BikeDocApiService
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
}

class DefaultBikeListRepository
    @Inject
    constructor(
        private val apiService: BikeDocApiService,
    ) : BikeListRepository {
        override suspend fun getBikes(): ApiResult<List<BikeListItem>> =
            com.bikedoc.android.api.safeApiCall {
                apiService.getBikes().items.map { bike ->
                    BikeListItem(
                        id = bike.id,
                        name = bike.displayName,
                        makeModelYear = buildMakeModelYear(bike.make, bike.model, bike.modelYear),
                        specificationSummary = buildSpecificationSummary(bike.drivetrain, bike.brakeType),
                        hasRepairSessions = bike.hasRepairSessions,
                    )
                }
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

        private fun buildSpecificationSummary(
            drivetrain: String?,
            brakeType: String?,
        ): String {
            val parts =
                listOfNotNull(
                    drivetrain?.takeIf { it.isNotBlank() },
                    brakeType
                        ?.takeIf { it.isNotBlank() }
                        ?.replace('_', ' ')
                        ?.split(' ')
                        ?.joinToString(" ") { part -> part.replaceFirstChar(Char::uppercaseChar) },
                )
            return parts.joinToString(separator = " • ").ifBlank { "Specifications coming soon" }
        }
    }
