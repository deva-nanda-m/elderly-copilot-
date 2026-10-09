import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import '../services/local_storage_service.dart';

/// Elderly-Friendly Authentication Screen
/// Designed specifically for senior citizens:
/// - Large high-contrast touch buttons (>= 56px height)
/// - Readable typography (18-26px)
/// - Bilingual Malayalam-primary labels with English subtitles
/// - Intuitive toggle between Login and Account Creation
class AuthScreen extends StatefulWidget {
  final Function(Map<String, String> session) onAuthenticated;

  const AuthScreen({super.key, required this.onAuthenticated});

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  bool isLoginMode = true;
  bool isLoading = false;
  String? errorMessage;

  final TextEditingController nameController = TextEditingController();
  final TextEditingController identifierController = TextEditingController();
  final TextEditingController passwordController = TextEditingController();
  final TextEditingController caregiverController = TextEditingController();
  final TextEditingController emergencyController = TextEditingController();

  static const String baseUrl = "http://127.0.0.1:8000";

  @override
  void dispose() {
    nameController.dispose();
    identifierController.dispose();
    passwordController.dispose();
    caregiverController.dispose();
    emergencyController.dispose();
    super.dispose();
  }

  Future<void> submit() async {
    final identifier = identifierController.text.trim();
    final password = passwordController.text.trim();

    if (identifier.isEmpty || password.isEmpty) {
      setState(() {
        errorMessage = "ദയവായി യൂസർനെയിമും പാസ്‌വേഡും നൽകുക\nPlease enter both username and password.";
      });
      return;
    }

    if (!isLoginMode && nameController.text.trim().isEmpty) {
      setState(() {
        errorMessage = "ദയവായി താങ്കളുടെ പേര് നൽകുക\nPlease enter your name.";
      });
      return;
    }

    setState(() {
      isLoading = true;
      errorMessage = null;
    });

    try {
      final String endpoint = isLoginMode
          ? "$baseUrl/api/auth/login"
          : "$baseUrl/api/auth/signup";

      final Map<String, dynamic> body = isLoginMode
          ? {
              "login_identifier": identifier,
              "password": password,
            }
          : {
              "name": nameController.text.trim(),
              "login_identifier": identifier,
              "password": password,
              "preferred_language": "ml",
              "caregiver_name": caregiverController.text.trim().isEmpty
                  ? null
                  : caregiverController.text.trim(),
              "emergency_contact": emergencyController.text.trim().isEmpty
                  ? null
                  : emergencyController.text.trim(),
            };

      final response = await http.post(
        Uri.parse(endpoint),
        headers: {"Content-Type": "application/json"},
        body: jsonEncode(body),
      );

      final data = jsonDecode(utf8.decode(response.bodyBytes));

      if (response.statusCode == 200 || response.statusCode == 201) {
        final token = data["access_token"] as String;
        final elder = data["elder"] as Map<String, dynamic>;
        final elderId = elder["elder_id"] as String;
        final name = elder["name"] as String;
        final caregiverCode = (elder["caregiver_linking_code"] ?? "") as String;
        final language = (elder["preferred_language"] ?? "ml") as String;

        // Persist session locally so elder doesn't need to re-login
        await LocalStorageService.saveSession(
          token: token,
          elderId: elderId,
          name: name,
          caregiverLinkingCode: caregiverCode,
          preferredLanguage: language,
        );

        widget.onAuthenticated({
          'token': token,
          'elder_id': elderId,
          'name': name,
          'caregiver_linking_code': caregiverCode,
          'preferred_language': language,
        });
      } else {
        setState(() {
          errorMessage = data["detail"] ??
              "പ്രവർത്തനം പരാജയപ്പെട്ടു. ദയവായി വീണ്ടും ശ്രമിക്കുക.\nAuthentication failed. Please try again.";
        });
      }
    } catch (e) {
      setState(() {
        errorMessage =
            "സെർവറുമായി ബന്ധപ്പെടാൻ കഴിഞ്ഞില്ല. സെർവർ പ്രവർത്തിക്കുന്നുണ്ടെന്ന് ഉറപ്പാക്കുക.\nCould not reach server. Please ensure backend is running.";
      });
    } finally {
      if (mounted) {
        setState(() => isLoading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0D1B1E),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 28),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 500),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Friendly Robot Companion Avatar
                  Center(
                    child: Container(
                      width: 100,
                      height: 100,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: const Color(0xFF1E3A3A),
                        border: Border.all(
                          color: const Color(0xFF4D8F80),
                          width: 3,
                        ),
                        boxShadow: [
                          BoxShadow(
                            color: const Color(0xFF4D8F80).withValues(alpha: 0.3),
                            blurRadius: 20,
                            spreadRadius: 2,
                          )
                        ],
                      ),
                      child: const Icon(
                        Icons.smart_toy_rounded,
                        size: 56,
                        color: Color(0xFF7DE2D1),
                      ),
                    ),
                  ),
                  const SizedBox(height: 20),

                  // Header with clear Malayalam & English text
                  const Text(
                    "സ്വാഗതം / Welcome",
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 30,
                      fontWeight: FontWeight.bold,
                      color: Color(0xFFE8F1F2),
                      letterSpacing: 0.5,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    isLoginMode
                        ? "താങ്കളുടെ അക്കൗണ്ടിലേക്ക് കടക്കുക\nSign in to talk with Kunchu"
                        : "പുതിയ അക്കൗണ്ട് ആരംഭിക്കുക\nCreate an account to start your companion",
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 16,
                      color: Color(0xFF90A4AE),
                      height: 1.4,
                    ),
                  ),
                  const SizedBox(height: 28),

                  // Mode Toggle Tabs (Large touch targets)
                  Container(
                    decoration: BoxDecoration(
                      color: const Color(0xFF192A2D),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: const Color(0xFF2C4A4D)),
                    ),
                    padding: const EdgeInsets.all(4),
                    child: Row(
                      children: [
                        Expanded(
                          child: InkWell(
                            onTap: () {
                              setState(() {
                                isLoginMode = true;
                                errorMessage = null;
                              });
                            },
                            borderRadius: BorderRadius.circular(12),
                            child: Container(
                              padding: const EdgeInsets.symmetric(vertical: 14),
                              decoration: BoxDecoration(
                                color: isLoginMode
                                    ? const Color(0xFF4D8F80)
                                    : Colors.transparent,
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Text(
                                "ലോഗിൻ / Log In",
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                  fontSize: 18,
                                  fontWeight: FontWeight.bold,
                                  color: isLoginMode
                                      ? Colors.white
                                      : const Color(0xFFB0BEC5),
                                ),
                              ),
                            ),
                          ),
                        ),
                        Expanded(
                          child: InkWell(
                            onTap: () {
                              setState(() {
                                isLoginMode = false;
                                errorMessage = null;
                              });
                            },
                            borderRadius: BorderRadius.circular(12),
                            child: Container(
                              padding: const EdgeInsets.symmetric(vertical: 14),
                              decoration: BoxDecoration(
                                color: !isLoginMode
                                    ? const Color(0xFF4D8F80)
                                    : Colors.transparent,
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Text(
                                "രജിസ്റ്റർ / Sign Up",
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                  fontSize: 18,
                                  fontWeight: FontWeight.bold,
                                  color: !isLoginMode
                                      ? Colors.white
                                      : const Color(0xFFB0BEC5),
                                ),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),

                  // Error notification banner if any
                  if (errorMessage != null) ...[
                    Container(
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(
                        color: const Color(0xFF3E1F24),
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: const Color(0xFFE57373)),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.info_outline, color: Color(0xFFFF8A80), size: 28),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Text(
                              errorMessage!,
                              style: const TextStyle(
                                color: Color(0xFFFFEBEE),
                                fontSize: 15,
                                height: 1.3,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 18),
                  ],

                  // Form Fields
                  if (!isLoginMode) ...[
                    _buildField(
                      controller: nameController,
                      label: "താങ്കളുടെ പേര് (Your Name)",
                      hint: "ഉദാ: മാധവൻ നായർ / Madhavan",
                      icon: Icons.person_rounded,
                    ),
                    const SizedBox(height: 16),
                  ],

                  _buildField(
                    controller: identifierController,
                    label: "യൂസർനെയിം / ഫോൺ നമ്പർ (Username / Phone)",
                    hint: "ഉദാ: madhavan / 9876543210",
                    icon: Icons.account_circle_rounded,
                  ),
                  const SizedBox(height: 16),

                  _buildField(
                    controller: passwordController,
                    label: "പാസ്‌വേഡ് (Password)",
                    hint: "രഹസ്യകോഡ് നൽകുക",
                    icon: Icons.lock_rounded,
                    obscureText: true,
                  ),
                  const SizedBox(height: 16),

                  if (!isLoginMode) ...[
                    _buildField(
                      controller: caregiverController,
                      label: "കെയർടേക്കറുടെ പേര് (Caregiver Name - Optional)",
                      hint: "ഉദാ: മകൻ, മകൾ, അല്ലെങ്കിൽ നഴ്സ്",
                      icon: Icons.favorite_rounded,
                    ),
                    const SizedBox(height: 16),
                    _buildField(
                      controller: emergencyController,
                      label: "അടിയന്തര ഫോൺ നമ്പർ (Emergency Contact - Optional)",
                      hint: "+91 ...",
                      icon: Icons.phone_in_talk_rounded,
                      keyboardType: TextInputType.phone,
                    ),
                    const SizedBox(height: 16),
                  ],

                  const SizedBox(height: 12),

                  // Big Action Button
                  SizedBox(
                    height: 60,
                    child: ElevatedButton(
                      onPressed: isLoading ? null : submit,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF4D8F80),
                        foregroundColor: Colors.white,
                        elevation: 4,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(16),
                        ),
                      ),
                      child: isLoading
                          ? const SizedBox(
                              width: 28,
                              height: 28,
                              child: CircularProgressIndicator(
                                strokeWidth: 3,
                                valueColor: AlwaysStoppedAnimation<Color>(Colors.white),
                              ),
                            )
                          : Text(
                              isLoginMode
                                  ? "ഉള്ളിലേക്ക് കടക്കുക (Log In)"
                                  : "അക്കൗണ്ട് ആരംഭിക്കുക (Create Account)",
                              style: const TextStyle(
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                                letterSpacing: 0.5,
                              ),
                            ),
                    ),
                  ),

                  const SizedBox(height: 20),

                  // Privacy & Non-Medical Note
                  const Center(
                    child: Text(
                      "🔒 സ്വകാര്യ വിവരങ്ങൾ സുരക്ഷിതമായി സൂക്ഷിക്കുന്നു\nFor personal companionship and routine care assistance only.",
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        fontSize: 13,
                        color: Color(0xFF607D8B),
                        height: 1.4,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildField({
    required TextEditingController controller,
    required String label,
    required String hint,
    required IconData icon,
    bool obscureText = false,
    TextInputType keyboardType = TextInputType.text,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            color: Color(0xFFCFD8DC),
          ),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: controller,
          obscureText: obscureText,
          keyboardType: keyboardType,
          style: const TextStyle(fontSize: 18, color: Colors.white),
          decoration: InputDecoration(
            hintText: hint,
            hintStyle: const TextStyle(color: Color(0xFF546E7A), fontSize: 16),
            prefixIcon: Icon(icon, color: const Color(0xFF4D8F80), size: 24),
            filled: true,
            fillColor: const Color(0xFF142427),
            contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(14),
              borderSide: const BorderSide(color: Color(0xFF2C4A4D)),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(14),
              borderSide: const BorderSide(color: Color(0xFF2C4A4D)),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(14),
              borderSide: const BorderSide(color: Color(0xFF4D8F80), width: 2),
            ),
          ),
        ),
      ],
    );
  }
}
