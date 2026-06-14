package com.guardian.agent.vpn

/**
 * Parses IPv4/IPv6 UDP/DNS packets from the VPN TUN and builds response packets.
 */
internal object DnsPacketHandler {
    private const val IP4_PROTOCOL_UDP: Int = 17
    private const val IP6_NEXT_HEADER_UDP: Int = 17

    enum class IpVersion {
        V4,
        V6,
    }

    data class ParsedDnsQuery(
        val ipVersion: IpVersion,
        val rawPacket: ByteArray,
        val length: Int,
        val ipHeaderLength: Int,
        val srcIp: ByteArray,
        val dstIp: ByteArray,
        val udpSrcPort: Int,
        val udpDstPort: Int,
        val dnsPayload: ByteArray,
        val queryName: String,
        val queryType: Int,
    )

    fun parse(packet: ByteArray, length: Int): ParsedDnsQuery? {
        if (length < 28) return null
        return when ((packet[0].toInt() shr 4) and 0xF) {
            4 -> parseIpv4(packet, length)
            6 -> parseIpv6(packet, length)
            else -> null
        }
    }

    fun minimalQuery(dnsPayload: ByteArray, queryName: String): ParsedDnsQuery {
        return ParsedDnsQuery(
            ipVersion = IpVersion.V4,
            rawPacket = ByteArray(0),
            length = 0,
            ipHeaderLength = 20,
            srcIp = ByteArray(4),
            dstIp = ByteArray(4),
            udpSrcPort = 0,
            udpDstPort = 53,
            dnsPayload = dnsPayload,
            queryName = queryName,
            queryType = DnsAnswerBuilder.queryType(dnsPayload),
        )
    }

    fun describeParseFailure(packet: ByteArray, length: Int): String {
        val version = (packet[0].toInt() shr 4) and 0xF
        if (version != 6) return "ipVersion=$version"
        val payloadLength = readUint16(packet, 4)
        val udpOffset = findIpv6UdpOffset(packet, length)
            ?: return "noUdpOffset nextHeader=${packet[6].toInt() and 0xFF} payloadLength=$payloadLength"
        val dstPort = readUint16(packet, udpOffset + 2)
        val udpLength = readUint16(packet, udpOffset + 4)
        val dnsLength = udpLength - 8
        return "udpOffset=$udpOffset dstPort=$dstPort udpLength=$udpLength dnsLength=$dnsLength"
    }

    fun buildResponse(query: ParsedDnsQuery, dnsResponse: ByteArray): ByteArray {
        return when (query.ipVersion) {
            IpVersion.V4 -> buildIpv4Response(query, dnsResponse)
            IpVersion.V6 -> buildIpv6Response(query, dnsResponse)
        }
    }

    private fun parseIpv4(packet: ByteArray, length: Int): ParsedDnsQuery? {
        val ipHeaderLength = (packet[0].toInt() and 0xF) * 4
        if (ipHeaderLength < 20) return null
        if ((packet[9].toInt() and 0xFF) != IP4_PROTOCOL_UDP) return null
        if (length < ipHeaderLength + 8) return null

        val udpOffset = ipHeaderLength
        val udpLength = readUint16(packet, udpOffset + 4)
        if (udpLength < 8 || length < udpOffset + udpLength) return null

        val srcPort = readUint16(packet, udpOffset)
        val dstPort = readUint16(packet, udpOffset + 2)
        if (dstPort != 53) return null

        val dnsOffset = udpOffset + 8
        val dnsLength = udpLength - 8
        if (dnsLength < 12) return null

        val dnsPayload = packet.copyOfRange(dnsOffset, dnsOffset + dnsLength)
        val queryName = extractQueryName(dnsPayload) ?: return null

        return ParsedDnsQuery(
            ipVersion = IpVersion.V4,
            rawPacket = packet,
            length = length,
            ipHeaderLength = ipHeaderLength,
            srcIp = packet.copyOfRange(12, 16),
            dstIp = packet.copyOfRange(16, 20),
            udpSrcPort = srcPort,
            udpDstPort = dstPort,
            dnsPayload = dnsPayload,
            queryName = queryName,
            queryType = DnsAnswerBuilder.queryType(dnsPayload),
        )
    }

    private fun parseIpv6(packet: ByteArray, length: Int): ParsedDnsQuery? {
        if (length < 48) return null

        val payloadLength = readUint16(packet, 4)
        if (payloadLength < 8 || length < 40 + payloadLength) return null

        val udpOffset = findIpv6UdpOffset(packet, length) ?: return null
        val udpLength = readUint16(packet, udpOffset + 4)
        if (udpLength < 8 || udpOffset + udpLength > length) return null

        val srcPort = readUint16(packet, udpOffset)
        val dstPort = readUint16(packet, udpOffset + 2)
        if (dstPort != 53) return null

        val dnsOffset = udpOffset + 8
        val dnsLength = udpLength - 8
        if (dnsLength < 12) return null

        val dnsPayload = packet.copyOfRange(dnsOffset, dnsOffset + dnsLength)
        val queryName = extractQueryName(dnsPayload) ?: return null

        return ParsedDnsQuery(
            ipVersion = IpVersion.V6,
            rawPacket = packet,
            length = length,
            ipHeaderLength = 40,
            srcIp = packet.copyOfRange(8, 24),
            dstIp = packet.copyOfRange(24, 40),
            udpSrcPort = srcPort,
            udpDstPort = dstPort,
            dnsPayload = dnsPayload,
            queryName = queryName,
            queryType = DnsAnswerBuilder.queryType(dnsPayload),
        )
    }

    private fun findIpv6UdpOffset(packet: ByteArray, length: Int): Int? {
        var nextHeader = packet[6].toInt() and 0xFF
        var offset = 40
        var hops = 0
        while (hops < 8) {
            when (nextHeader) {
                IP6_NEXT_HEADER_UDP -> return offset
                0, 43, 44, 60 -> {
                    if (offset + 8 > length) return null
                    nextHeader = packet[offset].toInt() and 0xFF
                    val extLen = ((packet[offset + 1].toInt() and 0xFF) + 1) * 8
                    offset += extLen
                    if (offset >= length) return null
                }
                else -> return null
            }
            hops++
        }
        return null
    }

    private fun buildIpv4Response(query: ParsedDnsQuery, dnsResponse: ByteArray): ByteArray {
        val udpLength = 8 + dnsResponse.size
        val totalLength = query.ipHeaderLength + udpLength
        val packet = ByteArray(totalLength)

        packet[0] = 0x45.toByte()
        packet[1] = 0
        writeUint16(packet, 2, totalLength)
        packet[8] = 64
        packet[9] = IP4_PROTOCOL_UDP.toByte()
        System.arraycopy(query.dstIp, 0, packet, 12, 4)
        System.arraycopy(query.srcIp, 0, packet, 16, 4)

        val udpOffset = query.ipHeaderLength
        writeUint16(packet, udpOffset, query.udpDstPort)
        writeUint16(packet, udpOffset + 2, query.udpSrcPort)
        writeUint16(packet, udpOffset + 4, udpLength)
        System.arraycopy(dnsResponse, 0, packet, udpOffset + 8, dnsResponse.size)

        writeIpChecksum(packet, query.ipHeaderLength)
        return packet
    }

    private fun buildIpv6Response(query: ParsedDnsQuery, dnsResponse: ByteArray): ByteArray {
        val udpLength = 8 + dnsResponse.size
        val payloadLength = udpLength
        val totalLength = 40 + payloadLength
        val packet = ByteArray(totalLength)

        System.arraycopy(query.rawPacket, 0, packet, 0, 4)
        packet[0] = 0x60.toByte()
        writeUint16(packet, 4, payloadLength)
        packet[6] = IP6_NEXT_HEADER_UDP.toByte()
        packet[7] = 64
        System.arraycopy(query.dstIp, 0, packet, 8, 16)
        System.arraycopy(query.srcIp, 0, packet, 24, 16)

        val udpOffset = 40
        writeUint16(packet, udpOffset, query.udpDstPort)
        writeUint16(packet, udpOffset + 2, query.udpSrcPort)
        writeUint16(packet, udpOffset + 4, udpLength)
        System.arraycopy(dnsResponse, 0, packet, udpOffset + 8, dnsResponse.size)
        return packet
    }

    private fun extractQueryName(dnsPayload: ByteArray): String? {
        if (dnsPayload.size <= 12) return null
        val qdCount = readUint16(dnsPayload, 4)
        if (qdCount < 1) return null

        var offset = 12
        val labels = mutableListOf<String>()
        while (offset < dnsPayload.size) {
            val len = dnsPayload[offset].toInt() and 0xFF
            if (len == 0) {
                break
            }
            if (len >= 192) return null
            offset++
            if (offset + len > dnsPayload.size) return null
            labels.add(String(dnsPayload, offset, len, Charsets.UTF_8))
            offset += len
        }
        if (labels.isEmpty()) return null
        return labels.joinToString(".")
    }

    private fun readUint16(packet: ByteArray, offset: Int): Int {
        return ((packet[offset].toInt() and 0xFF) shl 8) or (packet[offset + 1].toInt() and 0xFF)
    }

    private fun writeUint16(packet: ByteArray, offset: Int, value: Int) {
        packet[offset] = ((value shr 8) and 0xFF).toByte()
        packet[offset + 1] = (value and 0xFF).toByte()
    }

    private fun writeIpChecksum(packet: ByteArray, headerLength: Int) {
        packet[10] = 0
        packet[11] = 0
        val checksum = ipChecksum(packet, 0, headerLength)
        writeUint16(packet, 10, checksum)
    }

    private fun ipChecksum(data: ByteArray, offset: Int, length: Int): Int {
        var sum = 0
        var index = offset
        val end = offset + length
        while (index < end - 1) {
            sum += ((data[index].toInt() and 0xFF) shl 8) or (data[index + 1].toInt() and 0xFF)
            index += 2
        }
        if (length % 2 == 1) {
            sum += (data[end - 1].toInt() and 0xFF) shl 8
        }
        while (sum ushr 16 != 0) {
            sum = (sum and 0xFFFF) + (sum ushr 16)
        }
        return sum.inv() and 0xFFFF
    }
}
