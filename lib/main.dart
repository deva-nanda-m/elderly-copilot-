import 'dart:convert';
import 'dart:math' as math;

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:http/http.dart' as http;
import 'package:speech_to_text/speech_to_text.dart' as stt;

import 'services/local_storage_service.dart';
import 'screens/auth_screen.dart';

void main() {
  runApp(const ElderCareApp());
}

// ============================================================
// APP - WITH SESSION RESTORATION & AUTH STATE
// ============================================================

class ElderCareApp extends StatefulWidget {
  const ElderCareApp({super.key});

  @override
  State<ElderCareApp> createState() => _ElderCareAppState();
}

class _ElderCareAppState extends State<ElderCareApp> {
  bool isCheckingSession = true;
  Map<String, String>? activeSession;

  @override
  void initState() {
    super.initState();
    _checkActiveSession();
  }

  Future<void> _checkActiveSession() async {
    final session = await LocalStorageService.getActiveSession();
    if (mounted) {
      setState(() {
        activeSession = session;
        isCheckingSession = false;
      });
    }
  }

  void _onAuthenticated(Map<String, String> session) {
    setState(() {
      activeSession = session;
    });
  }

  void _onLogout() {
    setState(() {
      activeSession = null;
    });
    LocalStorageService.clearSession();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'ElderCare Copilot',
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF72B8A7),
        ),
        scaffoldBackgroundColor: const Color(0xFFF4F8F6),
        fontFamily: 'Arial',
      ),
      home: isCheckingSession
          ? const Scaffold(
              backgroundColor: Color(0xFF0D1B1E),
              body: Center(
                child: CircularProgressIndicator(color: Color(0xFF4D8F80)),
              ),
            )
          : (activeSession == null
              ? AuthScreen(
                  key: const ValueKey('auth_screen'),
                  onAuthenticated: _onAuthenticated,
                )
              : ElderCareHome(
                  key: ValueKey('elder_${activeSession!['elder_id']}'),
                  session: activeSession!,
                  onLogout: _onLogout,
                )),
    );
  }
}

// ============================================================
// CHAT MESSAGE MODEL
// ============================================================

class ChatMessage {
  final String id;
  final String elderId;
  final String sessionId;
  final String text;
  final bool isUser;
  final String? audioUrl;
  final DateTime timestamp;
  final String syncStatus; // 'synced', 'pending', 'failed'

  ChatMessage({
    String? id,
    String? elderId,
    String? sessionId,
    required this.text,
    required this.isUser,
    this.audioUrl,
    DateTime? timestamp,
    this.syncStatus = 'synced',
  })  : id = id ?? 'msg_${elderId ?? "elder"}_${DateTime.now().millisecondsSinceEpoch}_${isUser ? "u" : "a"}',
        elderId = elderId ?? 'elder_default',
        sessionId = sessionId ?? 'session_default',
        timestamp = timestamp ?? DateTime.now();
}

// ============================================================
// HOME SCREEN
// ============================================================

class ElderCareHome extends StatefulWidget {
  final Map<String, String> session;
  final VoidCallback onLogout;

  const ElderCareHome({
    super.key,
    required this.session,
    required this.onLogout,
  });

  @override
  State<ElderCareHome> createState() => _ElderCareHomeState();
}

// ============================================================
// HOME SCREEN STATE
// ============================================================

class _ElderCareHomeState extends State<ElderCareHome>
    with TickerProviderStateMixin {
  // ----------------------------------------------------------
  // CONTROLLERS
  // ----------------------------------------------------------

  final TextEditingController messageController = TextEditingController();
  final ScrollController scrollController = ScrollController();

  // Audio & Voice engines
  final AudioPlayer audioPlayer = AudioPlayer();
  final FlutterTts flutterTts = FlutterTts();
  final stt.SpeechToText speechToText = stt.SpeechToText();

  // ----------------------------------------------------------
  // CHAT MESSAGES
  // ----------------------------------------------------------

  final List<ChatMessage> messages = [];

  // ----------------------------------------------------------
  // ANIMATION CONTROLLERS
  // ----------------------------------------------------------

  late AnimationController robotController;
  late AnimationController speakingWaveController;

  // ----------------------------------------------------------
  // STATES
  // ----------------------------------------------------------

  bool isThinking = false;
  bool isSpeaking = false;
  bool isListening = false;
  bool isSubmittingVoice = false;
  bool speechEnabled = false;
  String speechLocaleId = 'ml_IN';
  int _speechToken = 0;

  // ----------------------------------------------------------
  // FASTAPI BACKEND URLS & SESSION
  // ----------------------------------------------------------

  static const String baseUrl = "http://127.0.0.1:8000";
  static const String chatUrl = "$baseUrl/chat";

  String get elderId => widget.session['elder_id'] ?? 'elder_default';
  String get elderName => widget.session['name'] ?? 'അപ്പൂപ്പൻ';
  String get token => widget.session['token'] ?? '';
  late final String sessionId;

  // ----------------------------------------------------------
  // STEP 3: PERSISTENCE & SYNC STATES
  // ----------------------------------------------------------

  bool isLoadingEarlier = false;
  bool hasEarlierMessages = true;
  String? offlineNotice;
  ChatMessage? pendingRetryMessage;

  // ==========================================================
  // LOCAL HISTORY PERSISTENCE (SQLITE)
  // ==========================================================

  Future<void> _loadLocalHistory() async {
    final cached = await LocalStorageService.getChatHistory(
      elderId: elderId,
      limit: 30,
    );
    if (mounted) {
      setState(() {
        messages.clear();
        for (final item in cached) {
          messages.add(
            ChatMessage(
              id: item['id']?.toString(),
              elderId: item['elder_id']?.toString() ?? elderId,
              sessionId: item['session_id']?.toString() ?? sessionId,
              text: item['text']?.toString() ?? '',
              isUser: (item['role'] == 'user' || item['is_user'] == true),
              audioUrl: item['audio_url']?.toString(),
              timestamp: item['timestamp'] != null
                  ? DateTime.tryParse(item['timestamp'].toString()) ?? DateTime.now()
                  : DateTime.now(),
              syncStatus: item['sync_status']?.toString() ?? 'synced',
            ),
          );
        }
        hasEarlierMessages = cached.length >= 30;
      });
      scrollToBottom();

      // Trigger duplicate-safe sync for any offline messages
      _triggerBackgroundSync();
    }
  }

  Future<void> _loadEarlierMessages() async {
    if (isLoadingEarlier || messages.isEmpty) return;
    setState(() => isLoadingEarlier = true);

    try {
      final oldestTime = messages.first.timestamp;
      final earlier = await LocalStorageService.getEarlierMessages(
        elderId: elderId,
        beforeTimestamp: oldestTime,
        limit: 20,
      );

      if (mounted) {
        setState(() {
          if (earlier.isEmpty) {
            hasEarlierMessages = false;
          } else {
            final prepended = earlier.map((item) {
              return ChatMessage(
                id: item['id']?.toString(),
                elderId: item['elder_id']?.toString() ?? elderId,
                sessionId: item['session_id']?.toString() ?? sessionId,
                text: item['text']?.toString() ?? '',
                isUser: (item['role'] == 'user' || item['is_user'] == true),
                audioUrl: item['audio_url']?.toString(),
                timestamp: item['timestamp'] != null
                    ? DateTime.tryParse(item['timestamp'].toString()) ?? DateTime.now()
                    : DateTime.now(),
                syncStatus: item['sync_status']?.toString() ?? 'synced',
              );
            }).toList();

            messages.insertAll(0, prepended);
            if (earlier.length < 20) {
              hasEarlierMessages = false;
            }
          }
        });
      }
    } catch (e) {
      debugPrint("Error loading earlier messages: $e");
    } finally {
      if (mounted) {
        setState(() => isLoadingEarlier = false);
      }
    }
  }

  Future<void> _triggerBackgroundSync() async {
    if (token.isEmpty) return;
    try {
      await LocalStorageService.syncPendingQueue(
        elderId: elderId,
        token: token,
        baseUrl: baseUrl,
      );
    } catch (_) {}
  }

  // ==========================================================
  // INIT
  // ==========================================================

  @override
  void initState() {
    super.initState();
    sessionId = "session_${DateTime.now().millisecondsSinceEpoch}";
    _loadLocalHistory();

    // Robot breathing and floating animation
    robotController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1800),
    )..repeat(reverse: true);

    // Speaking visualizer animation - only runs when sound is actively playing
    speakingWaveController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 800),
    );

    setupAudioAndTTS();
    initSpeech();
  }

  // ==========================================================
  // AUDIO & TTS SETUP
  // ==========================================================

  Future<void> setupAudioAndTTS() async {
    // 1. AudioPlayer listeners for backend TTS streaming with animation sync
    audioPlayer.onPlayerStateChanged.listen((state) {
      if (!mounted) return;
      if (state == PlayerState.playing) {
        if (!isSpeaking) {
          setState(() => isSpeaking = true);
        }
        if (!speakingWaveController.isAnimating) {
          speakingWaveController.repeat();
        }
      } else if (state == PlayerState.completed ||
          state == PlayerState.stopped ||
          state == PlayerState.paused) {
        if (isSpeaking) {
          setState(() => isSpeaking = false);
        }
        if (speakingWaveController.isAnimating) {
          speakingWaveController.stop();
          speakingWaveController.reset();
        }
      }
    });

    audioPlayer.onPlayerComplete.listen((_) {
      if (!mounted) return;
      if (isSpeaking) {
        setState(() => isSpeaking = false);
      }
      speakingWaveController.stop();
      speakingWaveController.reset();
    });

    // Slight speedup for lively, friendly rhythm
    try {
      await audioPlayer.setPlaybackRate(1.04);
    } catch (_) {}

    // 2. Offline FlutterTts fallback setup - youthful little-boy pitch & pace
    try {
      await flutterTts.setLanguage("ml-IN");
      await flutterTts.setSpeechRate(0.48); // Natural, lively pace
      await flutterTts.setPitch(1.30); // Sweet, higher youthful little-boy tone
      await flutterTts.setVolume(1.0);

      flutterTts.setStartHandler(() {
        if (!mounted) return;
        setState(() => isSpeaking = true);
        if (!speakingWaveController.isAnimating) {
          speakingWaveController.repeat();
        }
      });

      flutterTts.setCompletionHandler(() {
        if (!mounted) return;
        setState(() => isSpeaking = false);
        speakingWaveController.stop();
        speakingWaveController.reset();
      });

      flutterTts.setCancelHandler(() {
        if (!mounted) return;
        setState(() => isSpeaking = false);
        speakingWaveController.stop();
        speakingWaveController.reset();
      });

      flutterTts.setErrorHandler((msg) {
        debugPrint("TTS fallback error: $msg");
        if (!mounted) return;
        setState(() => isSpeaking = false);
        speakingWaveController.stop();
        speakingWaveController.reset();
      });
    } catch (e) {
      debugPrint("TTS setup exception: $e");
    }
  }

  // ==========================================================
  // SPEECH TO TEXT SETUP (VOICE SPEAKING INPUT)
  // ==========================================================

  Future<void> initSpeech() async {
    try {
      final available = await speechToText.initialize(
        onError: (error) {
          debugPrint('STT Error: $error');
          if (mounted && isListening) {
            setState(() => isListening = false);
          }
        },
        onStatus: (status) {
          debugPrint('STT Status: $status');
          if (status == 'done' || status == 'notListening') {
            if (mounted && isListening) {
              setState(() => isListening = false);
              _finishVoiceInputAndSend();
            }
          }
        },
      );

      if (available) {
        final locales = await speechToText.locales();
        String selected = 'ml_IN';
        if (locales.isNotEmpty) {
          final ml = locales
              .where((l) =>
                  l.localeId.toLowerCase().startsWith('ml') ||
                  l.name.toLowerCase().contains('malayalam'))
              .firstOrNull;
          final en = locales
              .where((l) => l.localeId.toLowerCase().startsWith('en'))
              .firstOrNull;
          selected = ml?.localeId ??
              en?.localeId ??
              (locales.isNotEmpty ? locales.first.localeId : 'ml_IN');
        }
        setState(() {
          speechEnabled = true;
          speechLocaleId = selected;
        });
        debugPrint('STT initialized with locale: $speechLocaleId');
      } else {
        setState(() {
          speechEnabled = false;
        });
        debugPrint('STT not available on this platform/device');
      }
    } catch (e) {
      debugPrint('STT init exception: $e');
    }
  }

  // Safe submission of voice input that prevents duplicate sends
  void _finishVoiceInputAndSend() {
    if (isSubmittingVoice) return;
    final text = messageController.text.trim();
    if (text.isNotEmpty && !isThinking) {
      isSubmittingVoice = true;
      sendMessage().whenComplete(() {
        isSubmittingVoice = false;
      });
    }
  }

  // ==========================================================
  // TOGGLE VOICE INPUT (MICROPHONE)
  // ==========================================================

  Future<void> toggleListening() async {
    if (isListening) {
      await speechToText.stop();
      setState(() => isListening = false);
      _finishVoiceInputAndSend();
    } else {
      if (isSpeaking) {
        await stopSpeaking();
      }

      if (!speechEnabled) {
        await initSpeech();
      }

      if (speechEnabled) {
        setState(() {
          isListening = true;
        });
        messageController.clear();

        await speechToText.listen(
          onResult: (result) {
            if (!mounted) return;
            setState(() {
              messageController.text = result.recognizedWords;
            });
            // If final result is reached and valid, submit safely
            if (result.finalResult &&
                result.recognizedWords.trim().isNotEmpty) {
              speechToText.stop();
              setState(() => isListening = false);
              _finishVoiceInputAndSend();
            }
          },
          listenOptions: stt.SpeechListenOptions(
            listenMode: stt.ListenMode.dictation,
            pauseFor: const Duration(seconds: 3), // Accommodate elder pauses
            listenFor: const Duration(seconds: 45), // Generous speech window
            partialResults: true,
            localeId: speechLocaleId,
          ),
        );
      } else {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              "വോയിസ് ഇൻപുട്ട് ഈ ഉപകരണത്തിൽ ലഭ്യമല്ല. ദയവായി എഴുതി ചോദിക്കൂ.",
              style: TextStyle(fontSize: 15),
            ),
            backgroundColor: Color(0xFF4D8F80),
            duration: Duration(seconds: 3),
          ),
        );
      }
    }
  }

  // ==========================================================
  // SEND MESSAGE TO BACKEND
  // ==========================================================

  Future<void> sendMessage({ChatMessage? retryMsg}) async {
    final text = retryMsg != null
        ? retryMsg.text
        : messageController.text.trim();
    if (text.isEmpty || isThinking) return;

    if (retryMsg == null) {
      // Stop speaking or listening if active
      if (isListening) {
        await speechToText.stop();
        setState(() => isListening = false);
      }
      if (isSpeaking) {
        await stopSpeaking();
      }
    }

    final userMsgId = retryMsg?.id ??
        'msg_${elderId}_${DateTime.now().millisecondsSinceEpoch}_u';
    final userMsg = retryMsg ??
        ChatMessage(
          id: userMsgId,
          elderId: elderId,
          sessionId: sessionId,
          text: text,
          isUser: true,
          timestamp: DateTime.now(),
          syncStatus: 'pending',
        );

    // 1. SAVE USER MESSAGE TO SQLITE BEFORE NETWORK REQUEST
    await LocalStorageService.saveChatMessage(
      id: userMsg.id,
      elderId: userMsg.elderId,
      sessionId: userMsg.sessionId,
      role: 'user',
      text: userMsg.text,
      timestamp: userMsg.timestamp,
      syncStatus: 'pending',
    );

    if (retryMsg == null) {
      setState(() {
        messages.add(userMsg);
        isThinking = true;
        offlineNotice = null;
        pendingRetryMessage = null;
      });
      messageController.clear();
      scrollToBottom();
    } else {
      setState(() {
        isThinking = true;
        offlineNotice = null;
        pendingRetryMessage = null;
      });
    }

    try {
      // 2. Strict conversation context for Gemini: current elder & session only
      final matching = messages
          .where((m) => m.elderId == elderId && m.sessionId == sessionId && m.id != userMsg.id)
          .toList();
      final recentHistory = matching.length > 10 ? matching.sublist(matching.length - 10) : matching;
      final historyList = recentHistory
          .map((m) => {
                "role": m.isUser ? "user" : "model",
                "text": m.text,
              })
          .toList();

      final response = await http.post(
        Uri.parse(chatUrl),
        headers: {
          "Content-Type": "application/json",
          if (token.isNotEmpty) "Authorization": "Bearer $token",
        },
        body: jsonEncode({
          "message": text,
          "history": historyList,
          "elder_id": elderId,
          "session_id": sessionId,
          "message_id": userMsg.id,
        }),
      ).timeout(const Duration(seconds: 35));

      if (response.statusCode == 200) {
        final data = jsonDecode(utf8.decode(response.bodyBytes));
        final String reply = data["response"] ??
            "ക്ഷമിക്കണം അപ്പൂപ്പാ, എനിക്ക് മറുപടി നൽകാൻ കഴിഞ്ഞില്ല.";
        final String? audioUrl = data["audio_url"];
        final String assistantMsgId = data["message_id"] ??
            'msg_${elderId}_${DateTime.now().millisecondsSinceEpoch}_a';

        // 3. Mark user message as synced in SQLite
        await LocalStorageService.markMessageSynced(userMsg.id);

        // 4. Save Gemini response to SQLite
        final assistantMsg = ChatMessage(
          id: assistantMsgId,
          elderId: elderId,
          sessionId: sessionId,
          text: reply,
          isUser: false,
          audioUrl: audioUrl,
          timestamp: DateTime.now(),
          syncStatus: 'synced',
        );

        await LocalStorageService.saveChatMessage(
          id: assistantMsg.id,
          elderId: assistantMsg.elderId,
          sessionId: assistantMsg.sessionId,
          role: 'assistant',
          text: assistantMsg.text,
          audioUrl: assistantMsg.audioUrl,
          timestamp: assistantMsg.timestamp,
          syncStatus: 'synced',
        );

        if (mounted) {
          setState(() {
            // Update user message sync status in UI
            final idx = messages.indexWhere((m) => m.id == userMsg.id);
            if (idx != -1) {
              messages[idx] = ChatMessage(
                id: userMsg.id,
                elderId: userMsg.elderId,
                sessionId: userMsg.sessionId,
                text: userMsg.text,
                isUser: userMsg.isUser,
                timestamp: userMsg.timestamp,
                syncStatus: 'synced',
              );
            }
            messages.add(assistantMsg);
            isThinking = false;
            offlineNotice = null;
            pendingRetryMessage = null;
          });
          scrollToBottom();

          // Sync any previously queued offline records
          _triggerBackgroundSync();

          // Speak the response using the synchronized TTS engine
          await playSpeech(reply, audioUrl: audioUrl);
        }
      } else {
        _handleChatNetworkFailure(userMsg);
      }
    } catch (e) {
      _handleChatNetworkFailure(userMsg);
    }
  }

  void _handleChatNetworkFailure(ChatMessage userMsg) {
    if (!mounted) return;
    setState(() {
      isThinking = false;
      offlineNotice =
          "ഇന്റർനെറ്റ് ലഭ്യമല്ല. ചോദ്യം സേവ് ചെയ്തിട്ടുണ്ട്. താഴെ തൊട്ട് വീണ്ടും ശ്രമിക്കാം.";
      pendingRetryMessage = userMsg;
    });
    scrollToBottom();
  }

  // ==========================================================
  // PLAY SPEECH (FIXED TTS ENGINE WITH RELIABLE FALLBACK)
  // ==========================================================

  Future<void> playSpeech(String text, {String? audioUrl}) async {
    final cleanText = text
        .replaceAll(RegExp(r'[*_#`~>\[\]\(\)\{\}\\"\/]'), '')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();

    if (cleanText.isEmpty) return;

    final thisToken = ++_speechToken;

    // Safely stop any ongoing speech synthesis
    try {
      await flutterTts.stop();
    } catch (_) {}

    // If another speech action started, bail out cleanly
    if (_speechToken != thisToken) return;

    bool streamStarted = false;

    // 1. Try high-fidelity streaming audio from FastAPI
    try {
      String streamUrl;
      if (audioUrl != null && audioUrl.isNotEmpty) {
        streamUrl = audioUrl.startsWith('http')
            ? audioUrl
            : '$baseUrl$audioUrl';
      } else {
        streamUrl = '$baseUrl/api/tts?text=${Uri.encodeComponent(cleanText)}';
      }

      if (_speechToken != thisToken) return;

      try {
        await audioPlayer.setVolume(1.0);
      } catch (_) {}

      await audioPlayer.play(UrlSource(streamUrl));
      if (_speechToken == thisToken) {
        streamStarted = true;
      }
    } catch (e) {
      debugPrint("AudioPlayer stream playback failed: $e. Checking fallback.");
      // Only abort if user triggered a NEW speech request
      if (_speechToken != thisToken) return;
      streamStarted = false;
    }

    // 2. Immediate fallback to local flutter_tts ONLY if stream failed and wasn't superseded
    if (!streamStarted && _speechToken == thisToken) {
      try {
        await flutterTts.speak(cleanText);
      } catch (e) {
        debugPrint("TTS fallback failed: $e");
        if (mounted && isSpeaking) {
          setState(() => isSpeaking = false);
          speakingWaveController.stop();
          speakingWaveController.reset();
        }
      }
    }
  }

  // ==========================================================
  // STOP SPEAKING
  // ==========================================================

  Future<void> stopSpeaking() async {
    _speechToken++; // Invalidate any pending or resolving play calls
    try {
      await audioPlayer.stop();
    } catch (_) {}
    try {
      await flutterTts.stop();
    } catch (_) {}

    if (mounted) {
      setState(() {
        isSpeaking = false;
      });
      speakingWaveController.stop();
      speakingWaveController.reset();
    }
  }

  // ==========================================================
  // CONVERSATION HISTORY SHEET & REPLAY
  // ==========================================================

  void _showConversationSheet() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) {
        return Container(
          height: MediaQuery.of(context).size.height * 0.72,
          decoration: const BoxDecoration(
            color: Color(0xFFF4F8F6),
            borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
          ),
          child: Column(
            children: [
              Container(
                margin: const EdgeInsets.symmetric(vertical: 12),
                width: 44,
                height: 5,
                decoration: BoxDecoration(
                  color: Colors.black26,
                  borderRadius: BorderRadius.circular(3),
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 6),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Text(
                      "സംഭാഷണങ്ങൾ",
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                        color: Color(0xFF315D54),
                      ),
                    ),
                    IconButton(
                      icon: const Icon(Icons.close, color: Color(0xFF315D54)),
                      onPressed: () => Navigator.of(ctx).pop(),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Expanded(
                child: messages.isEmpty
                    ? const Center(
                        child: Text(
                          "സംഭാഷണം തുടങ്ങിയിട്ടില്ല. താഴെ മൈക്കിൽ തൊട്ട് പറയൂ!",
                          style: TextStyle(fontSize: 16, color: Colors.black45),
                        ),
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        itemCount: messages.length + (hasEarlierMessages ? 1 : 0),
                        itemBuilder: (context, index) {
                          if (hasEarlierMessages && index == 0) {
                            return Center(
                              child: Padding(
                                padding: const EdgeInsets.symmetric(vertical: 8),
                                child: isLoadingEarlier
                                    ? const SizedBox(
                                        width: 24,
                                        height: 24,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                          color: Color(0xFF4D8F80),
                                        ),
                                      )
                                    : TextButton.icon(
                                        onPressed: _loadEarlierMessages,
                                        icon: const Icon(Icons.history, size: 18, color: Color(0xFF315D54)),
                                        label: const Text(
                                          "പഴയ സന്ദേശങ്ങൾ കാണുക (Load Earlier)",
                                          style: TextStyle(
                                            color: Color(0xFF315D54),
                                            fontWeight: FontWeight.bold,
                                            fontSize: 13,
                                          ),
                                        ),
                                      ),
                              ),
                            );
                          }

                          final msgIndex = hasEarlierMessages ? index - 1 : index;
                          final msg = messages[msgIndex];
                          final timeStr =
                              "${msg.timestamp.hour.toString().padLeft(2, '0')}:${msg.timestamp.minute.toString().padLeft(2, '0')}";

                          return Align(
                            alignment: msg.isUser
                                ? Alignment.centerRight
                                : Alignment.centerLeft,
                            child: Container(
                              margin: const EdgeInsets.symmetric(vertical: 6),
                              padding: const EdgeInsets.all(14),
                              constraints: BoxConstraints(
                                maxWidth: MediaQuery.of(context).size.width * 0.82,
                              ),
                              decoration: BoxDecoration(
                                color: msg.isUser
                                    ? const Color(0xFFD3ECE5)
                                    : Colors.white,
                                borderRadius: BorderRadius.circular(16),
                                boxShadow: [
                                  BoxShadow(
                                    color: Colors.black.withValues(alpha: 0.04),
                                    blurRadius: 6,
                                    offset: const Offset(0, 2),
                                  ),
                                ],
                              ),
                              child: Column(
                                crossAxisAlignment: msg.isUser
                                    ? CrossAxisAlignment.end
                                    : CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Flexible(
                                        child: Text(
                                          msg.text,
                                          style: TextStyle(
                                            fontSize: 15,
                                            height: 1.42,
                                            color: msg.isUser
                                                ? const Color(0xFF1E3833)
                                                : const Color(0xFF2C3E3A),
                                          ),
                                        ),
                                      ),
                                      if (!msg.isUser) ...[
                                        const SizedBox(width: 8),
                                        GestureDetector(
                                          onTap: () {
                                            playSpeech(msg.text, audioUrl: msg.audioUrl);
                                          },
                                          child: Container(
                                            padding: const EdgeInsets.all(6),
                                            decoration: const BoxDecoration(
                                              color: Color(0xFFE8F4F1),
                                              shape: BoxShape.circle,
                                            ),
                                            child: const Icon(
                                              Icons.volume_up,
                                              size: 18,
                                              color: Color(0xFF4D8F80),
                                            ),
                                          ),
                                        ),
                                      ],
                                    ],
                                  ),
                                  const SizedBox(height: 5),
                                  Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Text(
                                        timeStr,
                                        style: TextStyle(
                                          fontSize: 11,
                                          color: msg.isUser
                                              ? const Color(0xFF437166)
                                              : Colors.black38,
                                        ),
                                      ),
                                      if (msg.isUser) ...[
                                        const SizedBox(width: 4),
                                        Icon(
                                          msg.syncStatus == 'synced'
                                              ? Icons.done_all
                                              : Icons.cloud_queue,
                                          size: 13,
                                          color: msg.syncStatus == 'synced'
                                              ? const Color(0xFF315D54)
                                              : const Color(0xFFE65100),
                                        ),
                                      ],
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
              ),
            ],
          ),
        );
      },
    );
  }

  // ==========================================================
  // CONNECTION ERROR
  // ==========================================================

  void showConnectionError() {
    if (!mounted) return;
    setState(() {
      isThinking = false;
      messages.add(
        ChatMessage(
          text: "ക്ഷമിക്കണം, ഇപ്പോൾ സെർവറുമായി ബന്ധപ്പെടാൻ കഴിയുന്നില്ല.",
          isUser: false,
        ),
      );
    });
    scrollToBottom();
  }

  // ==========================================================
  // SCROLL TO BOTTOM
  // ==========================================================

  void scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!scrollController.hasClients) return;
      scrollController.animateTo(
        scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
    });
  }

  // ==========================================================
  // DISPOSE
  // ==========================================================

  @override
  void dispose() {
    _speechToken++;
    robotController.dispose();
    speakingWaveController.dispose();
    messageController.dispose();
    scrollController.dispose();
    audioPlayer.dispose();
    flutterTts.stop();
    speechToText.stop();
    super.dispose();
  }

  // ==========================================================
  // LOGOUT HANDLER
  // ==========================================================

  void _confirmAndLogout() {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF142427),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
        ),
        title: const Text(
          "ലോഗ് ഔട്ട് ചെയ്യണോ?\nLog Out?",
          style: TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.bold),
        ),
        content: const Text(
          "അക്കൗണ്ടിൽ നിന്ന് പുറത്ത് കടക്കാൻ ആഗ്രഹിക്കുന്നുണ്ടോ?\nAre you sure you want to log out?",
          style: TextStyle(color: Color(0xFFB0BEC5), fontSize: 16),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text("അല്ല (Cancel)", style: TextStyle(color: Colors.white70, fontSize: 16)),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFD32F2F),
              foregroundColor: Colors.white,
              padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 10),
            ),
            onPressed: () async {
              Navigator.of(ctx).pop();
              try {
                if (isSpeaking) await stopSpeaking();
                if (isListening) await speechToText.stop();
              } catch (_) {}
              widget.onLogout();
            },
            child: const Text("ലോഗ് ഔട്ട് (Log Out)", style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  // ==========================================================
  // BUILD UI
  // ==========================================================

  @override
  Widget build(BuildContext context) {
    final lastMessage = messages.isNotEmpty ? messages.last : null;

    return Scaffold(
      backgroundColor: const Color(0xFFF4F8F6),
      resizeToAvoidBottomInset: false,
      body: SafeArea(
        child: Column(
          children: [
            // ------------------------------------------------
            // HEADER
            // ------------------------------------------------
            Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: 24,
                vertical: 16,
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        elderName,
                        style: const TextStyle(
                          fontSize: 24,
                          fontWeight: FontWeight.bold,
                          color: Color(0xFF315D54),
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        widget.session['caregiver_linking_code'] != null &&
                                widget.session['caregiver_linking_code']!.isNotEmpty
                            ? "കെയർ കോഡ്: ${widget.session['caregiver_linking_code']}"
                            : "നിങ്ങളുടെ കൂട്ടുകാരൻ",
                        style: const TextStyle(
                          fontSize: 14,
                          color: Color(0xFF71847E),
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ],
                  ),

                  // Status Indicator / Audio control / Logout
                  Row(
                    children: [
                      if (isSpeaking)
                        Container(
                          margin: const EdgeInsets.only(right: 8),
                          child: IconButton(
                            icon: const Icon(Icons.volume_off, color: Color(0xFF4D8F80)),
                            tooltip: "ശബ്ദം നിർത്തുക",
                            onPressed: stopSpeaking,
                          ),
                        ),
                      Container(
                        margin: const EdgeInsets.only(right: 8),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          shape: BoxShape.circle,
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black.withValues(alpha: 0.05),
                              blurRadius: 10,
                              offset: const Offset(0, 4),
                            ),
                          ],
                        ),
                        child: IconButton(
                          icon: const Icon(Icons.chat_bubble_outline, color: Color(0xFF4D8F80)),
                          tooltip: "സംഭാഷണങ്ങൾ",
                          onPressed: _showConversationSheet,
                        ),
                      ),
                      // Prominent Elderly-Friendly Logout Action Button
                      Container(
                        margin: const EdgeInsets.only(left: 6),
                        decoration: BoxDecoration(
                          color: const Color(0xFFFDE8E8),
                          borderRadius: BorderRadius.circular(16),
                          border: Border.all(color: const Color(0xFFFFCDD2)),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black.withValues(alpha: 0.05),
                              blurRadius: 6,
                              offset: const Offset(0, 2),
                            ),
                          ],
                        ),
                        child: InkWell(
                          onTap: _confirmAndLogout,
                          borderRadius: BorderRadius.circular(16),
                          child: const Padding(
                            padding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(Icons.logout_rounded, color: Color(0xFFD32F2F), size: 20),
                                SizedBox(width: 4),
                                Text(
                                  "ലോഗ് ഔട്ട്",
                                  style: TextStyle(
                                    color: Color(0xFFD32F2F),
                                    fontWeight: FontWeight.bold,
                                    fontSize: 14,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),

            // ------------------------------------------------
            // ROBOT WIDGET WITH LIVELY SPEAKING ANIMATIONS
            // ------------------------------------------------
            Expanded(
              flex: 5,
              child: Center(
                child: FittedBox(
                  fit: BoxFit.contain,
                  child: RobotWidget(
                    robotAnimation: robotController,
                    speakingAnimation: speakingWaveController,
                    isSpeaking: isSpeaking,
                    isThinking: isThinking,
                    isListening: isListening,
                    onTap: () {
                      if (isSpeaking) {
                        stopSpeaking();
                      } else if (!isThinking) {
                        toggleListening();
                      }
                    },
                  ),
                ),
              ),
            ),

            // ------------------------------------------------
            // STATUS BADGE
            // ------------------------------------------------
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 250),
              child: Container(
                key: ValueKey("$isSpeaking-$isThinking-$isListening"),
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 7),
                decoration: BoxDecoration(
                  color: isSpeaking
                      ? const Color(0xFFE5F5F0)
                      : isListening
                          ? const Color(0xFFFFEBEB)
                          : isThinking
                              ? const Color(0xFFEFF5F3)
                              : Colors.white,
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                    color: isSpeaking
                        ? const Color(0xFF72B8A7)
                        : isListening
                            ? const Color(0xFFEF9A9A)
                            : isThinking
                                ? const Color(0xFF9CCFC3)
                                : Colors.transparent,
                    width: 1.2,
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withValues(alpha: 0.04),
                      blurRadius: 8,
                      offset: const Offset(0, 2),
                    ),
                  ],
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (isSpeaking) ...[
                      const Icon(Icons.volume_up, size: 20, color: Color(0xFF4D8F80)),
                      const SizedBox(width: 8),
                      const Text(
                        "ഞാൻ സംസാരിക്കുകയാണ്...",
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFF315D54),
                        ),
                      ),
                    ] else if (isListening) ...[
                      Container(
                        width: 10,
                        height: 10,
                        decoration: const BoxDecoration(
                          color: Color(0xFFD32F2F),
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 8),
                      const Text(
                        "കേൾക്കുന്നു... പറയൂ",
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFFC62828),
                        ),
                      ),
                    ] else if (isThinking) ...[
                      const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Color(0xFF4D8F80),
                        ),
                      ),
                      const SizedBox(width: 8),
                      const Text(
                        "ഞാൻ ചിന്തിക്കുകയാണ്...",
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFF4D8F80),
                        ),
                      ),
                    ] else ...[
                      const Icon(Icons.mic_none, size: 20, color: Color(0xFF60756F)),
                      const SizedBox(width: 6),
                      const Text(
                        "മൈക്കിൽ തൊട്ട് സംസാരിക്കൂ",
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFF60756F),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),

            const SizedBox(height: 12),

            // ------------------------------------------------
            // LAST MESSAGE CARD WITH REPLAY AUDIO BUTTON
            // ------------------------------------------------
            if (lastMessage != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(18),
                    boxShadow: [
                      BoxShadow(
                        blurRadius: 15,
                        offset: const Offset(0, 4),
                        color: Colors.black.withValues(alpha: 0.05),
                      ),
                    ],
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          lastMessage.text,
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          textAlign: TextAlign.start,
                          style: TextStyle(
                            fontSize: 16,
                            height: 1.45,
                            color: lastMessage.isUser
                                ? const Color(0xFF1E3833)
                                : const Color(0xFF2C3E3A),
                            fontWeight: lastMessage.isUser
                                ? FontWeight.w500
                                : FontWeight.normal,
                          ),
                        ),
                      ),
                      if (!lastMessage.isUser) ...[
                        const SizedBox(width: 10),
                        GestureDetector(
                          onTap: () {
                            if (isSpeaking) {
                              stopSpeaking();
                            } else {
                              playSpeech(
                                lastMessage.text,
                                audioUrl: lastMessage.audioUrl,
                              );
                            }
                          },
                          child: Container(
                            padding: const EdgeInsets.all(9),
                            decoration: BoxDecoration(
                              color: isSpeaking
                                  ? const Color(0xFFFFEBEE)
                                  : const Color(0xFFE8F4F1),
                              shape: BoxShape.circle,
                            ),
                            child: Icon(
                              isSpeaking ? Icons.stop : Icons.volume_up_rounded,
                              color: isSpeaking
                                  ? const Color(0xFFD32F2F)
                                  : const Color(0xFF4D8F80),
                              size: 22,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ),

            if (offlineNotice != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 6),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                  decoration: BoxDecoration(
                    color: const Color(0xFFFFF3E0),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: const Color(0xFFFFB74D)),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withValues(alpha: 0.04),
                        blurRadius: 6,
                        offset: const Offset(0, 2),
                      ),
                    ],
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.cloud_off, color: Color(0xFFE65100), size: 22),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          offlineNotice!,
                          style: const TextStyle(
                            color: Color(0xFFBF360C),
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                      if (pendingRetryMessage != null)
                        TextButton(
                          onPressed: () => sendMessage(retryMsg: pendingRetryMessage),
                          style: TextButton.styleFrom(
                            backgroundColor: const Color(0xFFE65100),
                            foregroundColor: Colors.white,
                            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                          ),
                          child: const Text("ശ്രമിക്കുക", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
                        ),
                    ],
                  ),
                ),
              ),

            const SizedBox(height: 14),

            // ------------------------------------------------
            // BOTTOM BAR: VOICE SPEAKING BUTTON + TEXT INPUT
            // ------------------------------------------------
            Padding(
              padding: const EdgeInsets.fromLTRB(18, 0, 18, 18),
              child: Row(
                children: [
                  // Prominent Microphone Button (Voice Speaking Feature)
                  GestureDetector(
                    onTap: toggleListening,
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 250),
                      width: 58,
                      height: 58,
                      decoration: BoxDecoration(
                        color: isListening
                            ? const Color(0xFFE53935)
                            : const Color(0xFF72B8A7),
                        shape: BoxShape.circle,
                        boxShadow: [
                          BoxShadow(
                            color: (isListening
                                    ? const Color(0xFFE53935)
                                    : const Color(0xFF72B8A7))
                                .withValues(alpha: isListening ? 0.45 : 0.28),
                            blurRadius: isListening ? 16 : 8,
                            spreadRadius: isListening ? 4 : 1,
                            offset: const Offset(0, 4),
                          ),
                        ],
                      ),
                      child: Icon(
                        isListening ? Icons.mic : Icons.mic_none,
                        color: Colors.white,
                        size: 28,
                      ),
                    ),
                  ),

                  const SizedBox(width: 10),

                  // Text input container
                  Expanded(
                    child: Container(
                      decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(30),
                        boxShadow: [
                          BoxShadow(
                            blurRadius: 10,
                            offset: const Offset(0, 2),
                            color: Colors.black.withValues(alpha: 0.06),
                          ),
                        ],
                      ),
                      child: TextField(
                        controller: messageController,
                        textInputAction: TextInputAction.send,
                        onSubmitted: (_) => sendMessage(),
                        style: const TextStyle(fontSize: 16),
                        decoration: InputDecoration(
                          hintText: isListening
                              ? "കേൾക്കുന്നു..."
                              : "എന്തെങ്കിലും പറയൂ അല്ലെങ്കിൽ എഴുതൂ...",
                          hintStyle: TextStyle(
                            color: isListening
                                ? const Color(0xFFE53935)
                                : Colors.black38,
                          ),
                          border: InputBorder.none,
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 20,
                            vertical: 15,
                          ),
                        ),
                      ),
                    ),
                  ),

                  const SizedBox(width: 10),

                  // Send / Stop button
                  GestureDetector(
                    onTap: isSpeaking
                        ? stopSpeaking
                        : (isListening ? toggleListening : sendMessage),
                    child: Container(
                      width: 58,
                      height: 58,
                      decoration: BoxDecoration(
                        color: isSpeaking
                            ? const Color(0xFFE57373)
                            : const Color(0xFF5AA694),
                        shape: BoxShape.circle,
                        boxShadow: [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.1),
                            blurRadius: 8,
                            offset: const Offset(0, 3),
                          ),
                        ],
                      ),
                      child: Icon(
                        isSpeaking
                            ? Icons.stop
                            : (isListening ? Icons.check : Icons.send),
                        color: Colors.white,
                        size: 24,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ============================================================
// ROBOT WIDGET WITH DYNAMIC SPEAKING & LISTENING ANIMATIONS
// ============================================================

class RobotWidget extends StatelessWidget {
  final Animation<double> robotAnimation;
  final Animation<double> speakingAnimation;
  final bool isSpeaking;
  final bool isThinking;
  final bool isListening;
  final VoidCallback? onTap;

  const RobotWidget({
    super.key,
    required this.robotAnimation,
    required this.speakingAnimation,
    required this.isSpeaking,
    required this.isThinking,
    required this.isListening,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: Listenable.merge([robotAnimation, speakingAnimation]),
      builder: (context, child) {
        // Dynamic movement depending on state
        final double movement = isSpeaking
            ? robotAnimation.value * 14
            : isListening
                ? robotAnimation.value * 8
                : isThinking
                    ? robotAnimation.value * 7
                    : robotAnimation.value * 4;

        // Dynamic scale
        final double scale = isSpeaking
            ? 1.0 + robotAnimation.value * 0.04
            : isListening
                ? 1.0 + robotAnimation.value * 0.025
                : isThinking
                    ? 1.0 + robotAnimation.value * 0.02
                    : 1.0 + robotAnimation.value * 0.012;

        return GestureDetector(
          onTap: onTap,
          child: Stack(
            alignment: Alignment.center,
            clipBehavior: Clip.none,
            children: [
              // ==========================================
              // 1. SPEAKING PULSATING GLOW & SOUNDWAVE RINGS
              // ==========================================
              if (isSpeaking) ...[
                // Outer Radial Glow Halo
                Container(
                  width: 320,
                  height: 320,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: const Color(0xFF72B8A7).withValues(
                          alpha: 0.22 + speakingAnimation.value * 0.18,
                        ),
                        blurRadius: 55 + speakingAnimation.value * 25,
                        spreadRadius: 10 + speakingAnimation.value * 10,
                      ),
                    ],
                  ),
                ),

                // Pulsing Sound Wave Ring 1 (Inner expanding ripple)
                Container(
                  width: 270 + speakingAnimation.value * 25,
                  height: 270 + speakingAnimation.value * 25,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: const Color(0xFF72B8A7).withValues(
                        alpha: (1.0 - speakingAnimation.value) * 0.45,
                      ),
                      width: 3,
                    ),
                  ),
                ),

                // Pulsing Sound Wave Ring 2 (Outer expanding ripple)
                Container(
                  width: 310 + ((speakingAnimation.value + 0.5) % 1.0) * 35,
                  height: 310 + ((speakingAnimation.value + 0.5) % 1.0) * 35,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: const Color(0xFF5AA694).withValues(
                        alpha: (1.0 - ((speakingAnimation.value + 0.5) % 1.0)) * 0.35,
                      ),
                      width: 2.2,
                    ),
                  ),
                ),
              ],

              // ==========================================
              // 2. LISTENING PULSATING AURA (MIC ACTIVE)
              // ==========================================
              if (isListening) ...[
                Container(
                  width: 300,
                  height: 300,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: const Color(0xFFE53935).withValues(
                          alpha: 0.20 + robotAnimation.value * 0.15,
                        ),
                        blurRadius: 40 + robotAnimation.value * 20,
                        spreadRadius: 8 + robotAnimation.value * 8,
                      ),
                    ],
                  ),
                ),
                Container(
                  width: 280 + robotAnimation.value * 20,
                  height: 280 + robotAnimation.value * 20,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: const Color(0xFFE53935).withValues(
                        alpha: (1.0 - robotAnimation.value) * 0.40,
                      ),
                      width: 2.5,
                    ),
                  ),
                ),
              ],

              // ==========================================
              // 3. THINKING ORBIT RING
              // ==========================================
              if (isThinking)
                SizedBox(
                  width: 290,
                  height: 290,
                  child: CircularProgressIndicator(
                    value: 0.65 + robotAnimation.value * 0.25,
                    strokeWidth: 3,
                    color: const Color(0xFF72B8A7),
                  ),
                ),

              // ==========================================
              // 4. ROBOT IMAGE (BOUNCING & BREATHING)
              // ==========================================
              Transform.translate(
                offset: Offset(0, -movement),
                child: Transform.scale(
                  scale: scale,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(28),
                    child: Image.asset(
                      "assets/bmo.jpg",
                      width: 260,
                      height: 260,
                      fit: BoxFit.contain,
                    ),
                  ),
                ),
              ),

              // ==========================================
              // 5. ANIMATED AUDIO EQUALIZER BARS (SPEAKING)
              // ==========================================
              if (isSpeaking)
                Positioned(
                  bottom: -10,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.92),
                      borderRadius: BorderRadius.circular(16),
                      boxShadow: [
                        BoxShadow(
                          color: const Color(0xFF72B8A7).withValues(alpha: 0.25),
                          blurRadius: 10,
                          offset: const Offset(0, 3),
                        ),
                      ],
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: List.generate(7, (index) {
                        // Create varied bar heights using sine waves with index offsets
                        final double phase = (speakingAnimation.value * 2 * math.pi) +
                            (index * 0.85);
                        final double barHeight = 8 + (math.sin(phase).abs() * 18);

                        return Container(
                          margin: const EdgeInsets.symmetric(horizontal: 2.5),
                          width: 4.5,
                          height: barHeight,
                          decoration: BoxDecoration(
                            color: const Color(0xFF5AA694),
                            borderRadius: BorderRadius.circular(4),
                          ),
                        );
                      }),
                    ),
                  ),
                ),
            ],
          ),
        );
      },
    );
  }
}