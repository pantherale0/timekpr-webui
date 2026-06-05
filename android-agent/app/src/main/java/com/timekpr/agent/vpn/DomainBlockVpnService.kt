package com.timekpr.agent.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.ParcelFileDescriptor
import androidx.core.app.NotificationCompat
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.util.AndroidUsers
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.InetAddress
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Local VPN tunnel that drops DNS queries for blocked domains (TimeKpr web policies).
 * Routes device DNS through the tunnel similar to the Linux agent's iptables redirect + local DNS proxy.
 */
class DomainBlockVpnService : VpnService() {
    private var tunInterface: ParcelFileDescriptor? = null
    private val running = AtomicBoolean(false)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopTunnel()
            stopSelf()
            return START_NOT_STICKY
        }
        startTunnel()
        return START_STICKY
    }

    override fun onDestroy() {
        stopTunnel()
        super.onDestroy()
    }

    private fun startTunnel() {
        if (running.getAndSet(true)) return
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification())

        val builder = Builder()
            .setSession("TimeKpr Web Policy")
            .addAddress("10.111.0.1", 32)
            .addDnsServer("10.111.0.1")
            .addRoute("0.0.0.0", 0)
            .setBlocking(true)
            .setMetered(false)

        tunInterface = builder.establish()
        Thread({ processPackets() }, "timekpr-vpn").start()
    }

    private fun processPackets() {
        val tun = tunInterface ?: return
        val input = FileInputStream(tun.fileDescriptor)
        val output = FileOutputStream(tun.fileDescriptor)
        val packet = ByteBuffer.allocate(32767)
        val app = TimeKprApplication.from(this)
        val domainStore = app.domainPolicyStore
        val uid = AndroidUsers.currentLinuxUid(this).toString()
        val blocked = domainStore.blockedDomainsForUid(uid).ifEmpty { domainStore.allBlockedDomains() }

        while (running.get()) {
            packet.clear()
            val length = input.read(packet.array())
            if (length <= 0) continue
            packet.limit(length)

            if (shouldDropDnsPacket(packet, blocked, domainStore)) {
                continue
            }
            output.write(packet.array(), 0, length)
        }
    }

    private fun shouldDropDnsPacket(
        packet: ByteBuffer,
        blocked: Set<String>,
        domainStore: com.timekpr.agent.policy.DomainPolicyStore,
    ): Boolean {
        if (blocked.isEmpty()) return false
        val version = (packet.get(0).toInt() shr 4) and 0xF
        if (version != 4) return false
        val protocol = packet.get(9).toInt() and 0xFF
        if (protocol != 17) return false // UDP only for DNS interception in v1

        val ihl = (packet.get(0).toInt() and 0xF) * 4
        if (packet.limit() < ihl + 8) return false
        val destPort = ((packet.get(ihl + 2).toInt() and 0xFF) shl 8) or (packet.get(ihl + 3).toInt() and 0xFF)
        if (destPort != 53) return false

        val dnsOffset = ihl + 8
        if (packet.limit() <= dnsOffset + 12) return false
        val qdCount = ((packet.get(dnsOffset + 4).toInt() and 0xFF) shl 8) or (packet.get(dnsOffset + 5).toInt() and 0xFF)
        if (qdCount < 1) return false

        var offset = dnsOffset + 12
        val labels = mutableListOf<String>()
        while (offset < packet.limit()) {
            val len = packet.get(offset).toInt() and 0xFF
            if (len == 0) {
                offset++
                break
            }
            if (len >= 192) break
            offset++
            if (offset + len > packet.limit()) return false
            val label = ByteArray(len)
            packet.position(offset)
            packet.get(label)
            labels.add(String(label, Charsets.UTF_8))
            offset += len
        }
        val queryName = labels.joinToString(".")
        return domainStore.isDomainBlocked(queryName, blocked)
    }

    private fun stopTunnel() {
        running.set(false)
        tunInterface?.close()
        tunInterface = null
    }

    private fun buildNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.vpn_notification_title))
            .setContentText(getString(R.string.vpn_notification_body))
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setOngoing(true)
            .build()
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "TimeKpr VPN",
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "timekpr_vpn"
        private const val NOTIFICATION_ID = 1002
        private const val ACTION_STOP = "com.timekpr.agent.vpn.STOP"

        fun reconcile(context: Context) {
            val app = TimeKprApplication.from(context)
            val blocked = app.domainPolicyStore.allBlockedDomains()
            if (blocked.isEmpty()) {
                context.stopService(Intent(context, DomainBlockVpnService::class.java).setAction(ACTION_STOP))
                return
            }
            val prepare = VpnService.prepare(context)
            if (prepare != null) {
                prepare.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(prepare)
                return
            }
            context.startForegroundService(Intent(context, DomainBlockVpnService::class.java))
        }
    }
}
