package com.bikedoc.android.api

import com.bikedoc.android.api.models.ArtifactRefDto
import com.bikedoc.android.api.models.BikeSessionCreateDto
import com.bikedoc.android.api.models.BikeSessionDto
import com.bikedoc.android.api.models.BikeSessionListResponseDto
import com.bikedoc.android.api.models.ExecutionProgressDto
import com.bikedoc.android.api.models.InputChoiceDto
import com.bikedoc.android.api.models.InputRequestDto
import com.bikedoc.android.api.models.LatestReportsDto
import com.bikedoc.android.api.models.TurnAcceptedDto
import com.bikedoc.android.api.models.TurnCreateDto
import com.bikedoc.android.api.models.UserTurnMessageDto
import com.bikedoc.android.sessions.models.ArtifactRef
import com.bikedoc.android.sessions.models.BikeSession
import com.bikedoc.android.sessions.models.BikeSessionCreate
import com.bikedoc.android.sessions.models.BikeSessionListResponse
import com.bikedoc.android.sessions.models.ExecutionProgress
import com.bikedoc.android.sessions.models.InputChoice
import com.bikedoc.android.sessions.models.InputRequest
import com.bikedoc.android.sessions.models.LatestReports
import com.bikedoc.android.sessions.models.TurnAccepted
import com.bikedoc.android.sessions.models.TurnCreate
import com.bikedoc.android.sessions.models.UserTurnMessage

fun BikeSessionDto.toDomain(): BikeSession =
    BikeSession(
        id = id,
        userId = userId,
        bikeId = bikeId,
        workflow = workflow,
        phase = phase,
        status = status,
        safetyState = safetyState,
        currentInputRequest = currentInputRequest?.toDomain(),
        executionProgress = executionProgress?.toDomain(),
        latestReports = latestReports.toDomain(),
        latestEventId = latestEventId,
        createdAt = createdAt,
        updatedAt = updatedAt,
    )

fun BikeSessionCreate.toDto(): BikeSessionCreateDto =
    BikeSessionCreateDto(
        workflow = workflow,
        bikeId = bikeId,
    )

fun BikeSessionListResponseDto.toDomain(): BikeSessionListResponse =
    BikeSessionListResponse(
        items = items.map { it.toDomain() },
        nextCursor = nextCursor,
    )

fun InputRequestDto.toDomain(): InputRequest =
    InputRequest(
        id = id,
        type = type,
        prompt = prompt,
        required = required,
        acceptedMediaTypes = acceptedMediaTypes,
        choices = choices.map { it.toDomain() },
        minArtifacts = minArtifacts,
        maxArtifacts = maxArtifacts,
        createdAt = createdAt,
        metadata = metadata,
    )

fun InputChoiceDto.toDomain(): InputChoice =
    InputChoice(
        id = id,
        label = label,
    )

fun TurnCreate.toDto(): TurnCreateDto =
    TurnCreateDto(
        schemaVersion = schemaVersion,
        clientTurnId = clientTurnId,
        message = message.toDto(),
        respondsToInputRequestId = respondsToInputRequestId,
    )

fun UserTurnMessage.toDto(): UserTurnMessageDto =
    UserTurnMessageDto(
        text = text,
        artifactIds = artifactIds,
    )

fun TurnAcceptedDto.toDomain(): TurnAccepted =
    TurnAccepted(
        turnId = turnId,
        sessionId = sessionId,
        startEventId = startEventId,
        eventStreamUrl = eventStreamUrl,
        session = session.toDomain(),
    )

fun ArtifactRefDto.toDomain(): ArtifactRef =
    ArtifactRef(
        id = id,
        userId = userId,
        sessionId = sessionId,
        bikeId = bikeId,
        purpose = purpose,
        mediaType = mediaType,
        mimeType = mimeType,
        filename = filename,
        byteSize = byteSize,
        width = width,
        height = height,
        durationSeconds = durationSeconds,
        status = status,
        rejectionReason = rejectionReason,
        createdAt = createdAt,
    )

fun ExecutionProgressDto.toDomain(): ExecutionProgress =
    ExecutionProgress(
        currentStepIndex = currentStepIndex,
        totalSteps = totalSteps,
    )

fun LatestReportsDto.toDomain(): LatestReports =
    LatestReports(
        diagnosticReportId = diagnosticReportId,
        planReportId = planReportId,
        executionReportId = executionReportId,
        shopReferralReportId = shopReferralReportId,
    )
