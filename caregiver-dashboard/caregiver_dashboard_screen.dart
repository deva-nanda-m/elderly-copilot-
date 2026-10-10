import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:http/http.dart' as http;
import 'package:speech_to_text/speech_to_text.dart';
import 'package:webview_flutter/webview_flutter.dart';

/// Hosts the caregiver dashboard (web) inside the Flutter app.
/// Connects to real FastAPI caregiver endpoints for authentication and data.
/// Speech-to-text and text-to-speech run natively and communicate with the page
/// through a JavaScript channel called "FlutterVoice".
class CaregiverDashboardScreen extends StatefulWidget {
  final String backendBaseUrl;

  const CaregiverDashboardScreen({
    super.key,
    this.backendBaseUrl = 'http://127.0.0.1:8000',
  });

  @override
  State<CaregiverDashboardScreen> createState() =>
      _CaregiverDashboardScreenState();
}

class _CaregiverDashboardScreenState extends State<CaregiverDashboardScreen> {
  late final WebViewController _controller;
  final SpeechToText _stt = SpeechToText();
  final FlutterTts _tts = FlutterTts();
  bool _sttReady = false;

  @override
  void initState() {
    super.initState();
    _initVoice();

    final dashboardUri = Uri.parse('${widget.backendBaseUrl}/dashboard/');

    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFFF1F3F9))
      ..addJavaScriptChannel('FlutterVoice', onMessageReceived: _onWebMessage)
      ..loadRequest(dashboardUri);
  }

  Future<void> _initVoice() async {
    _sttReady = await _stt.initialize();
    await _tts.setSpeechRate(0.5);
    _tts.setCompletionHandler(
        () => _js('window.onNativeSpeakDone && window.onNativeSpeakDone()'));
  }

  void _js(String code) => _controller.runJavaScript(code);

  void _onWebMessage(JavaScriptMessage m) async {
    final msg = jsonDecode(m.message) as Map<String, dynamic>;
    switch (msg['type']) {
      case 'dictate':
      case 'listen':
        await _listen();
        break;
      case 'speak':
        await _stt.stop();
        await _tts.speak(msg['text'] as String);
        break;
      case 'stop':
        await _stt.stop();
        await _tts.stop();
        break;
      case 'login':
        await _handleLogin(msg['email'] as String, msg['password'] as String);
        break;
      case 'register':
        await _handleRegister(
          name: msg['name'] as String,
          email: msg['email'] as String,
          phone: msg['phone'] as String?,
          role: msg['role'] as String? ?? 'family',
          password: msg['password'] as String,
        );
        break;
      case 'logout':
        _js('localStorage.removeItem("caregiver_token");');
        break;
      case 'forgot':
        _js('alert("Password reset instructions queued for ${msg['email']}");');
        break;
    }
  }

  /// Real caregiver account registration via FastAPI backend
  Future<void> _handleRegister({
    required String name,
    required String email,
    required String? phone,
    required String role,
    required String password,
  }) async {
    try {
      final res = await http.post(
        Uri.parse('${widget.backendBaseUrl}/api/caregiver/signup'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'name': name,
          'email': email,
          'phone': phone,
          'role': role,
          'password': password,
        }),
      );

      if (res.statusCode == 200 || res.statusCode == 201) {
        final data = jsonDecode(res.body) as Map<String, dynamic>;
        final caregiverName = (data['caregiver'] as Map<String, dynamic>?)?['name'] ?? name;
        final token = data['access_token'] ?? '';
        final result = {'ok': true, 'name': caregiverName, 'token': token};
        _js('localStorage.setItem("caregiver_token", "$token");');
        _js('window.onRegisterResult(${jsonEncode(result)})');
      } else {
        final err = jsonDecode(res.body) as Map<String, dynamic>;
        final result = {'ok': false, 'error': err['detail'] ?? 'Registration failed.'};
        _js('window.onRegisterResult(${jsonEncode(result)})');
      }
    } catch (e) {
      final result = {
        'ok': false,
        'error': 'Cannot connect to backend server (${widget.backendBaseUrl}). Please check network.'
      };
      _js('window.onRegisterResult(${jsonEncode(result)})');
    }
  }

  /// Real caregiver login via FastAPI backend
  Future<void> _handleLogin(String email, String password) async {
    try {
      final res = await http.post(
        Uri.parse('${widget.backendBaseUrl}/api/caregiver/login'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'email': email, 'password': password}),
      );

      if (res.statusCode == 200) {
        final data = jsonDecode(res.body) as Map<String, dynamic>;
        final caregiverName = (data['caregiver'] as Map<String, dynamic>?)?['name'] ?? 'Caregiver';
        final token = data['access_token'] ?? '';
        final result = {'ok': true, 'name': caregiverName, 'token': token};
        _js('localStorage.setItem("caregiver_token", "$token");');
        _js('window.onLoginResult(${jsonEncode(result)})');
      } else {
        final err = jsonDecode(res.body) as Map<String, dynamic>;
        final result = {'ok': false, 'error': err['detail'] ?? 'Incorrect email or password.'};
        _js('window.onLoginResult(${jsonEncode(result)})');
      }
    } catch (e) {
      final result = {
        'ok': false,
        'error': 'Cannot connect to backend server (${widget.backendBaseUrl}). Please check network.'
      };
      _js('window.onLoginResult(${jsonEncode(result)})');
    }
  }

  Future<void> _listen() async {
    if (!_sttReady) {
      _js('window.onNativeSpeechResult("")');
      return;
    }
    await _stt.listen(
      listenFor: const Duration(seconds: 20),
      pauseFor: const Duration(seconds: 3),
      onResult: (r) {
        if (r.finalResult) {
          _js('window.onNativeSpeechResult(${jsonEncode(r.recognizedWords)})');
        }
      },
    );
  }

  /// Push real data from your backend into the dashboard, e.g.
  /// pushCareData({'elders': {...}})
  Future<void> pushCareData(Map<String, dynamic> data) =>
      _controller.runJavaScript('window.loadCareData(${jsonEncode(data)})');

  @override
  void dispose() {
    _stt.stop();
    _tts.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF1F3F9),
      body: WebViewWidget(controller: _controller),
    );
  }
}
