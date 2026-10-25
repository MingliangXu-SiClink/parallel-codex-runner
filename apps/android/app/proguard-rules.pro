-keep class com.tencent.mm.opensdk.** { *; }
-dontwarn com.tencent.mm.**

# Keep Compose value-class signatures intact in the Markdown renderer after R8.
-keep class com.mikepenz.markdown.compose.** { *; }
-keepclassmembers class androidx.compose.ui.text.TextRange { *; }
-keepclassmembers class androidx.compose.ui.graphics.Color { *; }
-keepattributes Signature,InnerClasses,EnclosingMethod

# JLaTeXMath stores this class name as a string and instantiates it through
# reflection when expanding LaTeX macros. R8 otherwise merges the class into
# an unrelated implementation, which drops its public no-argument constructor
# and makes macro expansion fail in release builds.
-keep class org.scilab.forge.jlatexmath.NewCommandMacro { *; }
