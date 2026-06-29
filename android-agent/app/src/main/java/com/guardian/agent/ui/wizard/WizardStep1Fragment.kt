package com.guardian.agent.ui.wizard

import android.Manifest
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.LinearInterpolator
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import com.guardian.agent.GuardianApplication
import com.guardian.agent.R
import com.guardian.agent.databinding.FragmentWizardStep1Binding
import com.guardian.agent.protocol.PairingQrPayload
import com.guardian.agent.service.AgentSessionCoordinator
import com.google.android.material.snackbar.Snackbar
import java.util.concurrent.Executors

class WizardStep1Fragment : Fragment() {

    private var _binding: FragmentWizardStep1Binding? = null
    private val binding get() = _binding!!
    private var cameraProvider: ProcessCameraProvider? = null
    private var barcodeAnalyzer: BarcodeAnalyzer? = null
    private var scanHandled = false
    private var overlayAnimator: ObjectAnimator? = null
    private val cameraExecutor = Executors.newSingleThreadExecutor()

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startCamera()
        } else {
            wizardHost()?.showError(R.string.wizard_camera_permission)
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentWizardStep1Binding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        startOverlayPulse()
        if (hasCameraPermission()) {
            startCamera()
        } else {
            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onDestroyView() {
        overlayAnimator?.cancel()
        barcodeAnalyzer?.shutdown()
        cameraProvider?.unbindAll()
        cameraExecutor.shutdown()
        _binding = null
        super.onDestroyView()
    }

    private fun startOverlayPulse() {
        overlayAnimator = ObjectAnimator.ofFloat(binding.scanOverlay, View.ALPHA, 1f, 0.5f).apply {
            duration = 1200
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            interpolator = LinearInterpolator()
            start()
        }
    }

    private fun hasCameraPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            requireContext(),
            Manifest.permission.CAMERA,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(requireContext())
        future.addListener({
            cameraProvider = future.get()
            bindCamera()
        }, ContextCompat.getMainExecutor(requireContext()))
    }

    private fun bindCamera() {
        val provider = cameraProvider ?: return
        provider.unbindAll()

        val preview = Preview.Builder().build().also {
            it.surfaceProvider = binding.cameraPreview.surfaceProvider
        }

        val analyzer = BarcodeAnalyzer { raw ->
            if (scanHandled) return@BarcodeAnalyzer
            activity?.runOnUiThread { handleScanResult(raw) }
        }
        barcodeAnalyzer = analyzer

        val analysis = ImageAnalysis.Builder()
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .build()
            .also { it.setAnalyzer(cameraExecutor, analyzer) }

        provider.bindToLifecycle(
            viewLifecycleOwner,
            CameraSelector.DEFAULT_BACK_CAMERA,
            preview,
            analysis,
        )
    }

    private fun handleScanResult(raw: String) {
        if (scanHandled) return
        val payload = PairingQrPayload.parse(raw)
        if (payload == null) {
            Snackbar.make(binding.root, R.string.wizard_error_pairing, Snackbar.LENGTH_SHORT).show()
            return
        }
        scanHandled = true
        stopCamera()
        val store = GuardianApplication.from(requireContext()).configStore
        store.applyPairingPayload(payload.serverUrl, payload.registrationToken)
        AgentSessionCoordinator.startMobileAgent(requireContext())
        wizardHost()?.onPairingComplete()
    }

    private fun stopCamera() {
        overlayAnimator?.cancel()
        barcodeAnalyzer?.shutdown()
        barcodeAnalyzer = null
        cameraProvider?.unbindAll()
    }

    private fun wizardHost(): WizardHost? = activity as? WizardHost
}
