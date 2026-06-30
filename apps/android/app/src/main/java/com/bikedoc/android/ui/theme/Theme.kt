package com.bikedoc.android.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val BikeDocColorScheme =
    lightColorScheme(
        primary = Color(0xFF1B3A4B),
        secondary = Color(0xFF2D6A4F),
        tertiary = Color(0xFF7A4E2D),
        background = Color(0xFFF8FAF9),
        surface = Color(0xFFFFFFFF),
        onPrimary = Color(0xFFFFFFFF),
        onSecondary = Color(0xFFFFFFFF),
        onTertiary = Color(0xFFFFFFFF),
    )

@Composable
fun BikeDocTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = BikeDocColorScheme,
        content = content,
    )
}
