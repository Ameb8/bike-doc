package com.bikedoc.android.sessions.chat

import android.content.ContentResolver
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import com.bikedoc.android.api.models.PreparedDiagnosticPhoto
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.util.UUID
import javax.inject.Inject

data class DiagnosticPhotoSelection(
    val uri: String,
    val displayName: String,
    val mimeType: String,
)

interface DiagnosticPhotoPreparer {
    suspend fun prepare(selection: DiagnosticPhotoSelection): PreparedDiagnosticPhoto
}

class ContentResolverDiagnosticPhotoPreparer
    @Inject
    constructor(
        private val contentResolver: ContentResolver,
        @com.bikedoc.android.core.IoDispatcher private val ioDispatcher: CoroutineDispatcher,
    ) : DiagnosticPhotoPreparer {
        override suspend fun prepare(selection: DiagnosticPhotoSelection): PreparedDiagnosticPhoto =
            withContext(ioDispatcher) {
                val uri = Uri.parse(selection.uri)
                val sourceMimeType =
                    selection.mimeType.takeIf { it.isNotBlank() }
                        ?: contentResolver.getType(uri)
                        ?: DEFAULT_IMAGE_MIME_TYPE
                val displayName =
                    selection.displayName.takeIf { it.isNotBlank() }
                        ?: contentResolver.displayName(uri)
                        ?: DEFAULT_IMAGE_FILE_NAME

                if (sourceMimeType in ACCEPTED_IMAGE_MIME_TYPES) {
                    PreparedDiagnosticPhoto(
                        bytes = contentResolver.readBytes(uri),
                        fileName = displayName.withExtension(sourceMimeType.fileExtension()),
                        mimeType = sourceMimeType,
                        clientArtifactId = UUID.randomUUID().toString(),
                    )
                } else {
                    PreparedDiagnosticPhoto(
                        bytes = decodeBitmap(uri).toJpegBytes(),
                        fileName = displayName.withExtension(".jpg"),
                        mimeType = DEFAULT_IMAGE_MIME_TYPE,
                        clientArtifactId = UUID.randomUUID().toString(),
                    )
                }
            }

        private fun ContentResolver.readBytes(uri: Uri): ByteArray =
            requireNotNull(openInputStream(uri)) { "Unable to open selected image." }.use { input ->
                input.readBytes()
            }

        private fun ContentResolver.displayName(uri: Uri): String? =
            query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                val columnIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (columnIndex >= 0 && cursor.moveToFirst()) {
                    cursor.getString(columnIndex)
                } else {
                    null
                }
            }

        private fun decodeBitmap(uri: Uri): Bitmap {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                return ImageDecoder.decodeBitmap(ImageDecoder.createSource(contentResolver, uri))
            }
            return requireNotNull(contentResolver.openInputStream(uri).use(BitmapFactory::decodeStream)) {
                "Unable to decode selected image."
            }
        }

        private fun Bitmap.toJpegBytes(): ByteArray =
            ByteArrayOutputStream().use { output ->
                check(compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, output)) {
                    "Unable to transcode selected image."
                }
                output.toByteArray()
            }

        private fun String.withExtension(extension: String): String {
            val stem = substringBeforeLast('.', missingDelimiterValue = this).ifBlank { "diagnostic-photo" }
            return stem + extension
        }

        private fun String.fileExtension(): String =
            when (this) {
                "image/png" -> ".png"
                "image/webp" -> ".webp"
                else -> ".jpg"
            }

        private companion object {
            val ACCEPTED_IMAGE_MIME_TYPES = setOf("image/jpeg", "image/png", "image/webp")
            const val DEFAULT_IMAGE_MIME_TYPE = "image/jpeg"
            const val DEFAULT_IMAGE_FILE_NAME = "diagnostic-photo.jpg"
            const val JPEG_QUALITY = 90
        }
    }
