package com.timekpr.agent.vpn

import java.io.ByteArrayOutputStream
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress

internal object DnsAnswerBuilder {
    const val QTYPE_A = 1
    const val QTYPE_AAAA = 28
    private const val CLASS_IN = 1
    private const val DEFAULT_TTL = 300
    fun queryType(dnsPayload: ByteArray): Int {
        val questionEnd = questionEndOffset(dnsPayload) ?: return -1
        if (questionEnd + 2 > dnsPayload.size) return -1
        return ((dnsPayload[questionEnd].toInt() and 0xFF) shl 8) or
            (dnsPayload[questionEnd + 1].toInt() and 0xFF)
    }

    fun buildAnswer(query: DnsPacketHandler.ParsedDnsQuery, addresses: Array<InetAddress>): ByteArray? {
        val qtype = query.queryType
        val records = when (qtype) {
            QTYPE_A -> addresses.filterIsInstance<Inet4Address>().map { it.address }
            QTYPE_AAAA -> addresses.filterIsInstance<Inet6Address>().map { it.address }
            else -> emptyList()
        }
        if (records.isEmpty()) {
            return buildResponse(query, rcode = 0, answerCount = 0)
        }

        val answers = ByteArrayOutputStream()
        records.forEach { rdata ->
            answers.write(byteArrayOf(0xC0.toByte(), 0x0C.toByte()))
            answers.write(qtype shr 8)
            answers.write(qtype and 0xFF)
            answers.write(0)
            answers.write(CLASS_IN)
            answers.write((DEFAULT_TTL ushr 24) and 0xFF)
            answers.write((DEFAULT_TTL ushr 16) and 0xFF)
            answers.write((DEFAULT_TTL ushr 8) and 0xFF)
            answers.write(DEFAULT_TTL and 0xFF)
            answers.write(rdata.size shr 8)
            answers.write(rdata.size and 0xFF)
            answers.write(rdata)
        }
        return buildResponse(query, rcode = 0, answerCount = records.size, answers = answers.toByteArray())
    }

    fun buildNxDomain(query: DnsPacketHandler.ParsedDnsQuery): ByteArray {
        return buildResponse(query, rcode = 3, answerCount = 0)
    }

    fun buildServFail(query: DnsPacketHandler.ParsedDnsQuery): ByteArray {
        return buildResponse(query, rcode = 2, answerCount = 0)
    }

    private fun buildResponse(
        query: DnsPacketHandler.ParsedDnsQuery,
        rcode: Int,
        answerCount: Int,
        answers: ByteArray = ByteArray(0),
    ): ByteArray {
        val questionLength = questionSectionLength(query.dnsPayload)
        val dnsResponse = ByteArray(12 + questionLength + answers.size)

        dnsResponse[0] = query.dnsPayload[0]
        dnsResponse[1] = query.dnsPayload[1]
        val recursionDesired = query.dnsPayload[2].toInt() and 0x01
        dnsResponse[2] = (0x80 or recursionDesired).toByte()
        dnsResponse[3] = (0x80 or (rcode and 0x0F)).toByte()
        dnsResponse[4] = query.dnsPayload[4]
        dnsResponse[5] = query.dnsPayload[5]
        dnsResponse[6] = ((answerCount shr 8) and 0xFF).toByte()
        dnsResponse[7] = (answerCount and 0xFF).toByte()

        System.arraycopy(query.dnsPayload, 12, dnsResponse, 12, questionLength)
        if (answers.isNotEmpty()) {
            System.arraycopy(answers, 0, dnsResponse, 12 + questionLength, answers.size)
        }
        return dnsResponse
    }

    private fun questionSectionLength(dnsPayload: ByteArray): Int {
        val questionEnd = questionEndOffset(dnsPayload) ?: return 0
        val qtypeClassEnd = questionEnd + 4
        return (qtypeClassEnd - 12).coerceAtLeast(0)
    }

    private fun questionEndOffset(dnsPayload: ByteArray): Int? {
        if (dnsPayload.size <= 12) return null
        var offset = 12
        while (offset < dnsPayload.size) {
            val len = dnsPayload[offset].toInt() and 0xFF
            if (len == 0) {
                return offset + 1
            }
            if (len >= 192) return null
            offset++
            if (offset + len > dnsPayload.size) return null
            offset += len
        }
        return null
    }
}
