package com.guardian.agent.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.Network
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.os.Process
import android.os.UserHandle
import android.util.Log
import androidx.core.app.NotificationCompat
import com.guardian.agent.R
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.policy.BlockedDomainMatcher
import com.guardian.agent.boot.SecondaryUserInitService
import com.guardian.agent.policy.DomainPolicyResolver
import com.guardian.agent.policy.PolicyIpcServer
import com.guardian.agent.policy.ProfileProvisioningStore
import com.guardian.agent.util.AgentLog
import com.guardian.agent.config.AgentConfigStore
import java.io.FileInputStream
import java.io.FileOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import android.os.Handler
import android.os.Looper

/**
 * DNS-only VPN tunnel that blocks queries for filtered domains (Guardian web policies).
 * Allowed queries are resolved on the underlying network and answered through the TUN.
 */
class DomainBlockVpnService : VpnService() {
    private var tunInterface: ParcelFileDescriptor? = null
    private val running = AtomicBoolean(false)
    private var upstreamResolver: UpstreamDnsResolver? = null
    private var packetExecutor: ExecutorService? = null
    private var tunDrainThread: Thread? = null
    private var upstreamNetwork: Network? = null

    private var vpnBlockedDomains = emptySet<String>()
    private var vpnAllowedDomains = emptySet<String>()
    private var vpnBlockedMatcher: BlockedDomainMatcher = BlockedDomainMatcher.EMPTY

    private val policyReloadReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent?) {
            if (intent?.action == ACTION_RELOAD_POLICY) {
                Executors.newSingleThreadExecutor().execute {
                    GuardianApplication.from(context).domainPolicyStore.restore()
                    fetchPolicyAndReconcile()
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        val filter = android.content.IntentFilter(ACTION_RELOAD_POLICY)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(policyReloadReceiver, filter, RECEIVER_EXPORTED)
        } else {
            registerReceiver(policyReloadReceiver, filter)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopTunnel()
            stopSelf()
            return START_NOT_STICKY
        }
        upstreamNetwork = readUpstreamNetwork(intent)
        if (intent?.action == ACTION_RELOAD_POLICY && running.get()) {
            fetchPolicyAndReconcile()
            return START_STICKY
        }
        startTunnel()
        return START_STICKY
    }

    override fun onDestroy() {
        stopTunnel()
        try {
            unregisterReceiver(policyReloadReceiver)
        } catch (_: Exception) {}
        super.onDestroy()
    }

    private fun fetchPolicyAndReconcile() {
        val userId = Process.myUid() / 100_000
        val policy = Companion.loadPolicyForUser(this, userId)
        vpnBlockedDomains = policy.blockedDomains
        vpnAllowedDomains = policy.allowedDomains
        vpnBlockedMatcher = BlockedDomainMatcher.from(vpnBlockedDomains)
        AgentLog.d(
            TAG,
            "Loaded policy for user $userId (key=${policy.policyUid}): " +
                "blocked=${vpnBlockedDomains.size}, allowed=${vpnAllowedDomains.size}",
        )
    }

    private fun startTunnel() {
        if (running.getAndSet(true)) return

        fetchPolicyAndReconcile()
        if (vpnBlockedMatcher.isEmpty()) {
            AgentLog.d(TAG, "No blocked domains configured; skipping VPN tunnel")
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
            AgentLog.wOnce(TAG, "no_dns", "No upstream DNS servers available; skipping VPN tunnel")
            running.set(false)
            stopSelf()
            return
        }

        val builder = Builder()
            .setSession("Guardian Web Policy")
            .addAddress(VPN_ADDRESS, VPN_PREFIX)
            .setBlocking(true)
            .allowBypass()
            .allowFamily(android.system.OsConstants.AF_INET)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            builder.setMetered(false)
        }

        for (dnsServer in dnsServers) {
            val host = dnsServer.hostAddress ?: continue
            builder.addDnsServer(host)
            builder.addRoute(host, 32)
        }

        network?.let { builder.setUnderlyingNetworks(arrayOf(it)) }

        tunInterface = builder.establish()
        if (tunInterface == null) {
            AgentLog.wOnce(TAG, "tun_failed", "Failed to establish VPN tunnel")
            running.set(false)
            stopSelf()
            return
        }

        AgentLog.d(
            TAG,
            "VPN started with ${vpnBlockedDomains.size} blocked domain(s); " +
                "upstreamNetwork=$network routedDns=${dnsServers.joinToString { it.hostAddress ?: "?" }}",
        )

        val executor = packetExecutor ?: return
        tunDrainThread = Thread({ processTunPackets(resolver, executor) }, "guardian-vpn").also {
            it.start()
        }
    }

    private fun processTunPackets(
        resolver: UpstreamDnsResolver,
        executor: ExecutorService,
    ) {
        val tun = tunInterface ?: return
        val input = FileInputStream(tun.fileDescriptor)
        val output = FileOutputStream(tun.fileDescriptor)
        val packet = ByteArray(32767)

        while (running.get()) {
            if (vpnBlockedMatcher.isEmpty()) {
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
                    val blockedDnsResponse = uniffi.guardian_agent.checkAndBuildBlockedResponse(
                        parsed.dnsPayload,
                        vpnBlockedDomains.toList(),
                        vpnAllowedDomains.toList()
                    )
                    val dnsPayload = if (blockedDnsResponse != null) {
                        Log.d(TAG, "Blocked TUN DNS query for ${parsed.queryName}")
                        BlockNotificationCoordinator.onDomainBlocked(this@DomainBlockVpnService, parsed.queryName)
                        blockedDnsResponse
                    } else {
                        resolver.resolve(parsed) ?: run {
                            AgentLog.d(TAG, "Failed to resolve TUN DNS query for ${parsed.queryName}")
                            DnsAnswerBuilder.buildServFail(parsed)
                        }
                    }
                    val responsePacket = DnsPacketHandler.buildResponse(parsed, dnsPayload)
                    synchronized(output) {
                        output.write(responsePacket)
                    }
                    Log.d(TAG, "Answered TUN DNS query for ${parsed.queryName} (${parsed.ipVersion})")
                } catch (e: Exception) {
                    AgentLog.wOnce(TAG, "tun_dns_${parsed.queryName}", "Failed to handle TUN DNS query for ${parsed.queryName}")
                }
            }
        }
    }

    private fun isDomainAllowed(queryDomain: String, allowedDomains: Set<String>): Boolean {
        if (allowedDomains.isEmpty()) return false
        return BlockedDomainMatcher.from(allowedDomains).isBlocked(queryDomain)
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
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Guardian VPN",
                NotificationManager.IMPORTANCE_LOW,
            )
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    companion object {
        private const val TAG = "DomainBlockVpn"
        private const val CHANNEL_ID = "guardian_vpn"
        private const val NOTIFICATION_ID = 1002
        const val ACTION_RELOAD_POLICY = "com.guardian.agent.vpn.ACTION_RELOAD_POLICY"
        private const val ACTION_STOP = "com.guardian.agent.vpn.STOP"
        private const val EXTRA_UPSTREAM_NETWORK = "com.guardian.agent.vpn.EXTRA_UPSTREAM_NETWORK"
        private const val VPN_ADDRESS = "10.111.0.1"
        private const val VPN_PREFIX = 32
        private const val RECONCILE_DEBOUNCE_MS = 400L

        private val reconcileHandler = Handler(Looper.getMainLooper())
        private var pendingReconcileContext: Context? = null
        private val lastPolicySignatureByUser = mutableMapOf<Int, String>()

        fun reconcile(context: Context) {
            pendingReconcileContext = context.applicationContext
            reconcileHandler.removeCallbacks(reconcileRunnable)
            reconcileHandler.postDelayed(reconcileRunnable, RECONCILE_DEBOUNCE_MS)
        }

        private val reconcileRunnable = Runnable {
            val ctx = pendingReconcileContext ?: return@Runnable
            reconcileImmediate(ctx)
        }

        private fun reconcileImmediate(context: Context) {
            val callingUserId = Process.myUid() / 100_000
            reconcileForUser(context, callingUserId)

            if (callingUserId == 0 && DeviceOwnerProvisioner.isDeviceOwner(context)) {
                fanOutSecondaryUsers(context)
            }
        }

        private fun fanOutSecondaryUsers(context: Context) {
            val configStore = GuardianApplication.from(context).configStore
            if (configStore.load().managementMode == AgentConfigStore.MANAGEMENT_MODE_EXCLUSIVE_DO) {
                return
            }
            val targets = ProfileProvisioningStore(context).allProvisionedUserIds().filter { it > 0 }
            if (targets.isEmpty()) return
            for (userId in targets) {
                SecondaryUserInitService.startOnUser(context, userId)
                val reloadIntent = Intent(ACTION_RELOAD_POLICY).setPackage(context.packageName)
                try {
                    userHandleForId(userId)?.let { handle ->
                        context.sendBroadcastAsUser(reloadIntent, handle)
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "Failed to send reload policy broadcast to user $userId", e)
                }
            }
        }

        private fun policySignature(policy: DomainPolicyResolver.VpnDomainPolicy): String {
            return "${policy.policyUid}:${policy.blockedDomains.size}:${policy.blockedDomains.hashCode()}"
        }

        private fun reconcileForUser(context: Context, userId: Int) {
            val policy = loadPolicyForUser(context, userId)
            val signature = policySignature(policy)
            if (signature == lastPolicySignatureByUser[userId]) {
                return
            }
            lastPolicySignatureByUser[userId] = signature

            if (policy.blockedDomains.isEmpty()) {
                lastPolicySignatureByUser.remove(userId)
                DeviceOwnerProvisioner.clearVpnAuthorization(context)
                context.stopService(
                    Intent(context, DomainBlockVpnService::class.java).setAction(ACTION_STOP),
                )
                return
            }

            DeviceOwnerProvisioner.applyManagedCapabilities(context)
            DeviceOwnerProvisioner.grantVpnAuthorization(context)

            val prepare = VpnService.prepare(context)
            if (prepare != null) {
                if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) {
                    prepare.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    context.startActivity(prepare)
                } else {
                    AgentLog.wOnce(
                        TAG,
                        "vpn_grant_failed_$userId",
                        "VPN consent not granted; always-on VPN setup failed for user $userId",
                    )
                }
                return
            }

            val upstreamNetwork = VpnNetworkCapture.findUnderlyingNetwork(context)
            val serviceIntent = Intent(context, DomainBlockVpnService::class.java)
                .setAction(ACTION_RELOAD_POLICY)
                .putExtra(EXTRA_UPSTREAM_NETWORK, upstreamNetwork)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(serviceIntent)
            } else {
                context.startService(serviceIntent)
            }
        }

        private fun userHandleForId(userId: Int): UserHandle? {
            return try {
                val constructor = UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
                constructor.newInstance(userId) as UserHandle
            } catch (_: Exception) {
                null
            }
        }

        private fun loadPolicyForUser(context: Context, userId: Int): DomainPolicyResolver.VpnDomainPolicy {
            GuardianApplication.from(context).domainPolicyStore.restore()
            var policy = DomainPolicyResolver.loadVpnPolicyForUser(context, userId)
            if (policy.blockedDomains.isEmpty() && userId != 0) {
                fetchPolicyFromPrimaryUserIpc(userId)?.let { ipcPolicy ->
                    policy = ipcPolicy
                }
            }
            return policy
        }

        private fun fetchPolicyFromPrimaryUserIpc(androidUserId: Int): DomainPolicyResolver.VpnDomainPolicy? {
            val socket = android.net.LocalSocket()
            return try {
                socket.connect(android.net.LocalSocketAddress(PolicyIpcServer.SOCKET_NAME))
                val writer = java.io.PrintWriter(socket.outputStream, true)
                val reader = java.io.BufferedReader(java.io.InputStreamReader(socket.inputStream))
                writer.println("GET_POLICY $androidUserId")
                val responseLine = reader.readLine() ?: return null
                val json = org.json.JSONObject(responseLine)
                val blocked = mutableSetOf<String>()
                json.optJSONArray("blocked_domains")?.let { array ->
                    for (i in 0 until array.length()) {
                        blocked.add(array.getString(i))
                    }
                }
                val allowed = mutableSetOf<String>()
                json.optJSONArray("allowed_domains")?.let { array ->
                    for (i in 0 until array.length()) {
                        allowed.add(array.getString(i))
                    }
                }
                DomainPolicyResolver.VpnDomainPolicy(
                    policyUid = androidUserId.toString(),
                    blockedDomains = blocked,
                    allowedDomains = allowed,
                )
            } catch (e: Exception) {
                AgentLog.wOnce(TAG, "ipc_$androidUserId", "IPC policy fetch failed for user $androidUserId")
                null
            } finally {
                try {
                    socket.close()
                } catch (_: Exception) {}
            }
        }
    }
}
