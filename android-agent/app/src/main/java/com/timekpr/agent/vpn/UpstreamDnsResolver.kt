package com.timekpr.agent.vpn

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.VpnService
import android.util.Log
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.UnknownHostException

internal class UpstreamDnsResolver(
    context: Context,
    private val vpnService: VpnService,
    upstreamNetwork: Network?,
) {
    private val connectivityManager =
        context.getSystemService(ConnectivityManager::class.java)

    val network: Network? = upstreamNetwork ?: VpnNetworkCapture.findUnderlyingNetwork(context)
    val servers: List<InetAddress> = resolveUpstreamServers(network)

    fun resolve(parsed: DnsPacketHandler.ParsedDnsQuery): ByteArray? {
        return resolveRaw(parsed.dnsPayload, parsed.queryName)
    }

    fun resolveRaw(dnsPayload: ByteArray, queryName: String): ByteArray? {
        val query = DnsPacketHandler.minimalQuery(dnsPayload, queryName)
        if (query.queryType == DnsAnswerBuilder.QTYPE_A || query.queryType == DnsAnswerBuilder.QTYPE_AAAA) {
            resolveViaNetwork(query)?.let { return it }
        }
        return forward(dnsPayload)
    }

    private fun resolveViaNetwork(parsed: DnsPacketHandler.ParsedDnsQuery): ByteArray? {
        val upstream = network ?: return null
        return withBoundNetwork(upstream) {
            try {
                val addresses = upstream.getAllByName(parsed.queryName)
                if (addresses.isEmpty()) {
                    null
                } else {
                    DnsAnswerBuilder.buildAnswer(parsed, addresses)
                }
            } catch (_: UnknownHostException) {
                DnsAnswerBuilder.buildNxDomain(parsed)
            } catch (e: Exception) {
                Log.w(TAG, "Network lookup failed for ${parsed.queryName}", e)
                null
            }
        }
    }

    private fun <T> withBoundNetwork(network: Network, block: () -> T): T {
        val manager = connectivityManager
        if (manager == null) {
            return block()
        }
        val previous = manager.boundNetworkForProcess
        return try {
            manager.bindProcessToNetwork(network)
            block()
        } finally {
            manager.bindProcessToNetwork(previous)
        }
    }

    fun forward(query: ByteArray): ByteArray? {
        for (server in servers) {
            try {
                DatagramSocket().use { socket ->
                    bindSocket(socket)
                    socket.soTimeout = TIMEOUT_MS
                    socket.send(DatagramPacket(query, query.size, server, 53))
                    val buffer = ByteArray(4096)
                    val response = DatagramPacket(buffer, buffer.size)
                    socket.receive(response)
                    return buffer.copyOf(response.length)
                }
            } catch (e: Exception) {
                Log.d(TAG, "Raw upstream DNS query failed via ${server.hostAddress}", e)
            }
        }
        return null
    }

    private fun bindSocket(socket: DatagramSocket) {
        val upstream = network
        if (upstream != null) {
            upstream.bindSocket(socket)
            return
        }
        if (!vpnService.protect(socket)) {
            throw IllegalStateException("Failed to protect upstream DNS socket")
        }
    }

    private fun resolveUpstreamServers(network: Network?): List<InetAddress> {
        val servers = linkedSetOf<InetAddress>()
        val linkProperties: LinkProperties? = network?.let { connectivityManager?.getLinkProperties(it) }
        linkProperties?.dnsServers
            ?.filterIsInstance<Inet4Address>()
            ?.forEach { servers.add(it) }
        FALLBACK_SERVERS.forEach { servers.add(it) }
        return servers.toList()
    }

    companion object {
        private const val TAG = "UpstreamDnsResolver"
        private const val TIMEOUT_MS = 3_000
        private val FALLBACK_SERVERS = listOf(
            InetAddress.getByName("8.8.8.8"),
            InetAddress.getByName("1.1.1.1"),
        )
    }
}
