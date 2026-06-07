package com.timekpr.agent.util

import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserFactory
import java.io.File
import java.io.StringReader

object PrefXmlReader {
    fun stringValues(file: File): Map<String, String> {
        if (!file.exists()) return emptyMap()
        return try {
            parseStringValues(file.readText())
        } catch (_: Exception) {
            emptyMap()
        }
    }

    fun parseStringValues(xml: String): Map<String, String> {
        val values = linkedMapOf<String, String>()
        val factory = XmlPullParserFactory.newInstance()
        val parser = factory.newPullParser()
        parser.setInput(StringReader(xml))
        var event = parser.eventType
        while (event != XmlPullParser.END_DOCUMENT) {
            if (event == XmlPullParser.START_TAG && parser.name == "string") {
                val name = parser.getAttributeValue(null, "name")
                if (!name.isNullOrBlank()) {
                    values[name] = parser.nextText()
                }
            }
            event = parser.next()
        }
        return values
    }
}
