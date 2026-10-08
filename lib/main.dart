import 'dart:convert';
import 'dart:math' as math;

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:http/http.dart' as http;
import 'package:speech_to_text/speech_to_text.dart' as stt;

void main() {
  runApp(const ElderCareApp());
}

// ============================================================
// APP
// ============================================================

class ElderCareApp extends StatelessWidget {
  const ElderCareApp({super.key});

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
      home: const ElderCareHome(),
    );
  }
}

// ============================================================
// CHAT MESSAGE MODEL
// ============================================================

class ChatMessage {
  final String text;
  final bool isUser;
  final String? audioUrl;
  final DateTime timestamp;

  ChatMessage({
    required this.text,
    required this.isUser,
    this.audioUrl,
    DateTime? timestamp,
  }) : timestamp = timestamp ?? DateTime.now();
}

// ============================================================
// HOME SCREEN
// ============================================================

class ElderCareHome extends StatefulWidget {
  const ElderCareHome({super.key});

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
  bool speechEnabled = false;
  String speechLocaleId = 'ml_IN';

  // ----------------------------------------------------------
  // FASTAPI BACKEND URLS
  // ----------------------------------------------------------

  static const String baseUrl = "http://127.0.0.1:8000";
  static const String chatUrl = "$baseUrl/chat";

  // ==========================================================
  // INIT
  // ==========================================================

  @override
  void initState() {
    super.initState();

    // Robot breathing and floating animation
    robotController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1800),
    )..repeat(reverse: true);

    // Fast speaking visualizer pulse animation
    speakingWaveController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 800),
    )..repeat();

    setupAudioAndTTS();
    initSpeech();
  }

  // ==========================================================
  // AUDIO & TTS SETUP
  // ==========================================================

  Future<void> setupAudioAndTTS() async {
    // 1. AudioPlayer listeners for backend TTS streaming
    audioPlayer.onPlayerStateChanged.listen((state) {
      if (!mounted) return;
      if (state == PlayerState.playing) {
        if (!isSpeaking) setState(() => isSpeaking = true);
      } else if (state == PlayerState.completed ||
          state == PlayerState.stopped) {
        if (isSpeaking) setState(() => isSpeaking = false);
      }
    });

    audioPlayer.onPlayerComplete.listen((_) {
      if (!mounted) return;
      setState(() => isSpeaking = false);
    });

    // 2. Offline FlutterTts fallback setup
    try {
      await flutterTts.setLanguage("ml-IN");
      await flutterTts.setSpeechRate(0.42);
      await flutterTts.setVolume(1.0);
      await flutterTts.setPitch(1.0);

      flutterTts.setStartHandler(() {
        if (!mounted) return;
        setState(() => isSpeaking = true);
      });

      flutterTts.setCompletionHandler(() {
        if (!mounted) return;
        setState(() => isSpeaking = false);
      });

      flutterTts.setCancelHandler(() {
        if (!mounted) return;
        setState(() => isSpeaking = false);
      });

      flutterTts.setErrorHandler((msg) {
        debugPrint("TTS fallback error: $msg");
        if (!mounted) return;
        setState(() => isSpeaking = false);
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
              if (messageController.text.trim().isNotEmpty) {
                sendMessage();
              }
            }
          }
        },
      );

      if (available) {
        final locales = await speechToText.locales();
        String selected = 'ml_IN';
        if (locales.isNotEmpty) {
          final ml = locales
              .where((l) => l.localeId.toLowerCase().startsWith('ml'))
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

  // ==========================================================
  // TOGGLE VOICE INPUT (MICROPHONE)
  // ==========================================================

  Future<void> toggleListening() async {
    if (isListening) {
      await speechToText.stop();
      setState(() => isListening = false);
      if (messageController.text.trim().isNotEmpty) {
        sendMessage();
      }
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
            if (result.finalResult &&
                result.recognizedWords.trim().isNotEmpty) {
              speechToText.stop();
              setState(() => isListening = false);
              sendMessage();
            }
          },
          listenOptions: stt.SpeechListenOptions(
            listenMode: stt.ListenMode.confirmation,
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

  Future<void> sendMessage() async {
    final text = messageController.text.trim();
    if (text.isEmpty || isThinking) return;

    // Stop speaking or listening if active
    if (isListening) {
      await speechToText.stop();
      setState(() => isListening = false);
    }
    if (isSpeaking) {
      await stopSpeaking();
    }

    setState(() {
      messages.add(
        ChatMessage(
          text: text,
          isUser: true,
        ),
      );
      isThinking = true;
    });

    messageController.clear();
    scrollToBottom();

    try {
      final response = await http.post(
        Uri.parse(chatUrl),
        headers: {
          "Content-Type": "application/json",
        },
        body: jsonEncode({
          "message": text,
        }),
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(utf8.decode(response.bodyBytes));
        final String reply = data["response"] ??
            "ക്ഷമിക്കണം, എനിക്ക് മറുപടി നൽകാൻ കഴിഞ്ഞില്ല.";
        final String? audioUrl = data["audio_url"];

        setState(() {
          messages.add(
            ChatMessage(
              text: reply,
              isUser: false,
              audioUrl: audioUrl,
            ),
          );
          isThinking = false;
        });

        scrollToBottom();

        // Speak the response using the fixed TTS engine
        await playSpeech(reply, audioUrl: audioUrl);
      } else {
        debugPrint("Server error: ${response.statusCode}");
        showConnectionError();
      }
    } catch (e) {
      debugPrint("Connection error: $e");
      showConnectionError();
    }
  }

  // ==========================================================
  // PLAY SPEECH (FIXED TTS ENGINE)
  // ==========================================================

  Future<void> playSpeech(String text, {String? audioUrl}) async {
    final cleanText = text
        .replaceAll(RegExp(r'[*_#`~>\[\]]'), '')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();

    if (cleanText.isEmpty) return;

    await stopSpeaking();

    if (!mounted) return;
    setState(() {
      isSpeaking = true;
    });

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

      await audioPlayer.play(UrlSource(streamUrl));
      return;
    } catch (e) {
      debugPrint("AudioPlayer stream playback error: $e. Falling back to flutter_tts.");
    }

    // 2. Fallback to local flutter_tts
    try {
      await flutterTts.speak(cleanText);
    } catch (e) {
      debugPrint("TTS fallback failed: $e");
      if (mounted) setState(() => isSpeaking = false);
    }
  }

  // ==========================================================
  // STOP SPEAKING
  // ==========================================================

  Future<void> stopSpeaking() async {
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
    }
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
  // BUILD UI
  // ==========================================================

  @override
  Widget build(BuildContext context) {
    final lastMessage = messages.isNotEmpty ? messages.last : null;

    return Scaffold(
      backgroundColor: const Color(0xFFF4F8F6),
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
                    children: const [
                      Text(
                        "ElderCare",
                        style: TextStyle(
                          fontSize: 26,
                          fontWeight: FontWeight.bold,
                          color: Color(0xFF315D54),
                        ),
                      ),
                      SizedBox(height: 3),
                      Text(
                        "നിങ്ങളുടെ കൂട്ടുകാരൻ",
                        style: TextStyle(
                          fontSize: 15,
                          color: Color(0xFF71847E),
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ],
                  ),

                  // Status Indicator / Audio control
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
                        padding: const EdgeInsets.all(11),
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
                        child: const Icon(
                          Icons.favorite,
                          color: Color(0xFF72B8A7),
                          size: 24,
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