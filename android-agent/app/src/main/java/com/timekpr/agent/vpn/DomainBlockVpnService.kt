package com.timekpr.agent.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Network
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import androidx.core.app.NotificationCompat
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.admin.TimeKprDeviceAdminReceiver
import com.timekpr.agent.policy.DomainPolicyStore
import java.io.FileInputStream
import java.io.FileOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * DNS-only VPN tunnel that blocks queries for filtered domains (TimeKpr web policies).
 * Allowed queries are resolved on the underlying network and answered through the TUN.
 */
class DomainBlockVpnService : VpnService() {
    private var tunInterface: ParcelFileDescriptor? = null
    private val running = AtomicBoolean(false)
    private var upstreamResolver: UpstreamDnsResolver? = null
    private var packetExecutor: ExecutorService? = null
    private var tunDrainThread: Thread? = null
    private var upstreamNetwork: Network? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopTunnel()
            stopSelf()
            return START_NOT_STICKY
        }
        upstreamNetwork = readUpstreamNetwork(intent)
        startTunnel()
        return START_STICKY
    }

    override fun onDestroy() {
        stopTunnel()
        super.onDestroy()
    }

    private fun startTunnel() {
        if (running.getAndSet(true)) return

        val domainStore = TimeKprApplication.from(this).domainPolicyStore
        if (domainStore.allBlockedDomains().isEmpty()) {
            Log.i(TAG, "No blocked domains configured; skipping VPN tunnel")
            running.set(false)
            stopSelf()
            return
        }

        val network = upstreamNetwork ?: VpnNetworkCapture.findUnderlyingNetwork(this)
        upstreamResolver = UpstreamDnsResolver(this, this, network)
        packetExecutor = Executors.newFixedThreadPool(4)

        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification())

        val resolver = upstreamResolver ?: return
        val dnsServers = resolver.servers
        if (dnsServers.isEmpty()) {
            Log.w(TAG, "No upstream DNS servers available; skipping VPN tunnel")
            running.set(false)
            stopSelf()
            return
        }

        val builder = Builder()
            .setSession("TimeKpr Web Policy")
            .addAddress(VPN_ADDRESS, VPN_PREFIX)
            .setBlocking(true)
            .setMetered(false)
            .allowFamily(android.system.OsConstants.AF_INET)

        for (dnsServer in dnsServers) {
            val host = dnsServer.hostAddress ?: continue
            builder.addDnsServer(host)
            builder.addRoute(host, 32)
        }

        network?.let { builder.setUnderlyingNetworks(arrayOf(it)) }

        tunInterface = builder.establish()
        if (tunInterface == null) {
            Log.w(TAG, "Failed to establish VPN tunnel")
            running.set(false)
            stopSelf()
            return
        }

        Log.i(
            TAG,
            "VPN started with ${domainStore.allBlockedDomains().size} blocked domain(s); " +
                "upstreamNetwork=$network routedDns=${dnsServers.joinToString { it.hostAddress ?: "?" }}",
        )

        val executor = packetExecutor ?: return
        tunDrainThread = Thread({ processTunPackets(domainStore, resolver, executor) }, "timekpr-vpn").also {
            it.start()
        }
    }

    private fun processTunPackets(
        domainStore: DomainPolicyStore,
        resolver: UpstreamDnsResolver,
        executor: ExecutorService,
    ) {
        val tun = tunInterface ?: return
        val input = FileInputStream(tun.fileDescriptor)
        val output = FileOutputStream(tun.fileDescriptor)
        val packet = ByteArray(32767)

        while (running.get()) {
            val blocked = domainStore.allBlockedDomains()
            if (blocked.isEmpty()) {
                stopTunnel()
                stopSelf()
                break
            }

            val length = input.read(packet)
            if (length <= 0) continue

            val parsed = DnsPacketHandler.parse(packet, length)
            if (parsed == null) {
                Log.v(TAG, "Ignoring non-DNS TUN packet (${DnsPacketHandler.describeParseFailure(packet, length)})")
                continue
            }
            executor.execute {
                if (!running.get()) return@execute
                try {
                    val dnsPayload = if (domainStore.isDomainBlocked(parsed.queryName, blocked)) {
                        Log.d(TAG, "Blocked TUN DNS query for ${parsed.queryName}")
                        BlockNotificationCoordinator.onDomainBlocked(this@DomainBlockVpnService, parsed.queryName)
                        DnsAnswerBuilder.buildNxDomain(parsed)
                    } else {
                        resolver.resolve(parsed) ?: run {
                            Log.w(TAG, "Failed to resolve TUN DNS query for ${parsed.queryName}")
                            DnsAnswerBuilder.buildServFail(parsed)
                        }
                    }
                    val responsePacket = DnsPacketHandler.buildResponse(parsed, dnsPayload)
                    synchronized(output) {
                        output.write(responsePacket)
                    }
                    Log.d(TAG, "Answered TUN DNS query for ${parsed.queryName} (${parsed.ipVersion})")
                } catch (e: Exception) {
                    Log.w(TAG, "Failed to handle TUN DNS query for ${parsed.queryName}", e)
                }
            }
        }
    }

    private fun stopTunnel() {
        running.set(false)
        BlockNotificationCoordinator.onVpnStopped()
        tunDrainThread?.interrupt()
        tunInterface?.close()
        tunInterface = null
        packetExecutor?.shutdownNow()
        packetExecutor = null
        tunDrainThread = null
        upstreamResolver = null
        upstreamNetwork = null
    }

    private fun readUpstreamNetwork(intent: Intent?): Network? {
        if (intent == null) return null
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(EXTRA_UPSTREAM_NETWORK, Network::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(EXTRA_UPSTREAM_NETWORK)
        }
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
        private const val TAG = "DomainBlockVpn"
        private const val CHANNEL_ID = "timekpr_vpn"
        private const val NOTIFICATION_ID = 1002
        private const val ACTION_STOP = "com.timekpr.agent.vpn.STOP"
        private const val EXTRA_UPSTREAM_NETWORK = "com.timekpr.agent.vpn.EXTRA_UPSTREAM_NETWORK"
        private const val VPN_ADDRESS = "10.111.0.1"
        private const val VPN_PREFIX = 32
        fun reconcile(context: Context) {
            val app = TimeKprApplication.from(context)
            val blocked = app.domainPolicyStore.allBlockedDomains()
            if (blocked.isEmpty()) {
                clearAlwaysOnVpnIfDeviceOwner(context)
                context.stopService(Intent(context, DomainBlockVpnService::class.java).setAction(ACTION_STOP))
                return
            }

            DeviceOwnerProvisioner.applyIfDeviceOwner(context)
            setAlwaysOnVpnIfDeviceOwner(context)

            val prepare = VpnService.prepare(context)
            if (prepare != null) {
                prepare.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(prepare)
                return
            }

            val upstreamNetwork = VpnNetworkCapture.findUnderlyingNetwork(context)
            val serviceIntent = Intent(context, DomainBlockVpnService::class.java)
                .putExtra(EXTRA_UPSTREAM_NETWORK, upstreamNetwork)
            context.startForegroundService(serviceIntent)
        }

        private fun setAlwaysOnVpnIfDeviceOwner(context: Context) {
            if (!DeviceOwnerProvisioner.isDeviceOwner(context)) return
            val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
            val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
            try {
                dpm.setAlwaysOnVpnPackage(admin, context.packageName, false)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to set always-on VPN", e)
            }
        }

        private fun clearAlwaysOnVpnIfDeviceOwner(context: Context) {
            if (!DeviceOwnerProvisioner.isDeviceOwner(context)) return
            val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
            val admin = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
            try {
                dpm.setAlwaysOnVpnPackage(admin, null, false)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to clear always-on VPN", e)
            }
        }
    }
}
