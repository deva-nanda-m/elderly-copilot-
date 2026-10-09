import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

/// Multiplatform local storage repository for ElderCare Copilot.
/// Supports Chrome Web (via HTML5 LocalStorage) and Android (via Native SharedPreferences/SQLite).
/// Handles:
/// - Active session persistence & auto-login restoration
/// - Local offline conversation history
/// - Local offline medication schedule cache
/// - Offline sync queue with idempotency tracking
class LocalStorageService {
  static const String _keyToken = 'auth_token';
  static const String _keyElderId = 'elder_id';
  static const String _keyElderName = 'elder_name';
  static const String _keyCaregiverCode = 'caregiver_linking_code';
  static const String _keyLanguage = 'preferred_language';
  static const String _keyOfflineMessagesPrefix = 'offline_messages_';
  static const String _keyOfflineMedsPrefix = 'offline_meds_';
  static const String _keySyncQueue = 'pending_sync_queue';

  // Fast in-memory cache to guarantee zero-failure session operations
  static final Map<String, String> _memoryStore = {};

  // ---------------------------------------------------------------------------
  // AUTH SESSION MANAGEMENT
  // ---------------------------------------------------------------------------

  /// Persists active elder session so repeated logins are unnecessary
  static Future<void> saveSession({
    required String token,
    required String elderId,
    required String name,
    required String caregiverLinkingCode,
    String preferredLanguage = 'ml',
  }) async {
    _memoryStore[_keyToken] = token;
    _memoryStore[_keyElderId] = elderId;
    _memoryStore[_keyElderName] = name;
    _memoryStore[_keyCaregiverCode] = caregiverLinkingCode;
    _memoryStore[_keyLanguage] = preferredLanguage;

    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_keyToken, token);
      await prefs.setString(_keyElderId, elderId);
      await prefs.setString(_keyElderName, name);
      await prefs.setString(_keyCaregiverCode, caregiverLinkingCode);
      await prefs.setString(_keyLanguage, preferredLanguage);
    } catch (_) {
      // In-memory store handles session seamlessly if native plugin is unavailable
    }
  }

  /// Restores active session if valid token exists
  static Future<Map<String, String>?> getActiveSession() async {
    String? token = _memoryStore[_keyToken];
    String? elderId = _memoryStore[_keyElderId];
    String? name = _memoryStore[_keyElderName];
    String? caregiverCode = _memoryStore[_keyCaregiverCode];
    String? language = _memoryStore[_keyLanguage];

    try {
      final prefs = await SharedPreferences.getInstance();
      token = prefs.getString(_keyToken) ?? token;
      elderId = prefs.getString(_keyElderId) ?? elderId;
      name = prefs.getString(_keyElderName) ?? name;
      caregiverCode = prefs.getString(_keyCaregiverCode) ?? caregiverCode;
      language = prefs.getString(_keyLanguage) ?? language;
    } catch (_) {}

    if (token == null || elderId == null || token.isEmpty || elderId.isEmpty) {
      return null;
    }

    return {
      'token': token,
      'elder_id': elderId,
      'name': name ?? 'അപ്പൂപ്പൻ / അമ്മൂമ്മ',
      'caregiver_linking_code': caregiverCode ?? '',
      'preferred_language': language ?? 'ml',
    };
  }

  /// Clears active session on logout (guaranteed never to throw or hang)
  static Future<void> clearSession() async {
    _memoryStore.clear();

    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_keyToken);
      await prefs.remove(_keyElderId);
      await prefs.remove(_keyElderName);
      await prefs.remove(_keyCaregiverCode);
      await prefs.remove(_keyLanguage);
      await prefs.clear();
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // OFFLINE CHAT HISTORY PERSISTENCE
  // ---------------------------------------------------------------------------

  /// Persists conversation history locally across app restarts & offline periods
  static Future<void> saveLocalChatHistory(
    String elderId,
    List<Map<String, dynamic>> messages,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    // Keep up to 60 most recent messages locally
    final trimmed = messages.length > 60
        ? messages.sublist(messages.length - 60)
        : messages;
    await prefs.setString(
      '$_keyOfflineMessagesPrefix$elderId',
      jsonEncode(trimmed),
    );
  }

  /// Loads cached local conversation history
  static Future<List<Map<String, dynamic>>> getLocalChatHistory(
    String elderId,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString('$_keyOfflineMessagesPrefix$elderId');
    if (raw == null || raw.isEmpty) return [];
    try {
      final List<dynamic> decoded = jsonDecode(raw);
      return decoded.map((e) => Map<String, dynamic>.from(e)).toList();
    } catch (_) {
      return [];
    }
  }

  // ---------------------------------------------------------------------------
  // OFFLINE MEDICATION CACHE
  // ---------------------------------------------------------------------------

  /// Stores essential medication instructions locally for offline access
  static Future<void> saveLocalMedicines(
    String elderId,
    List<Map<String, dynamic>> medicines,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      '$_keyOfflineMedsPrefix$elderId',
      jsonEncode(medicines),
    );
  }

  static Future<List<Map<String, dynamic>>> getLocalMedicines(
    String elderId,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString('$_keyOfflineMedsPrefix$elderId');
    if (raw == null || raw.isEmpty) return [];
    try {
      final List<dynamic> decoded = jsonDecode(raw);
      return decoded.map((e) => Map<String, dynamic>.from(e)).toList();
    } catch (_) {
      return [];
    }
  }

  // ---------------------------------------------------------------------------
  // OFFLINE SYNC QUEUE
  // ---------------------------------------------------------------------------

  /// Queues an operation for synchronization when connectivity returns
  static Future<void> queueSyncOperation(Map<String, dynamic> operation) async {
    final prefs = await SharedPreferences.getInstance();
    final queue = await getPendingSyncQueue();
    queue.add(operation);
    await prefs.setString(_keySyncQueue, jsonEncode(queue));
  }

  static Future<List<Map<String, dynamic>>> getPendingSyncQueue() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_keySyncQueue);
    if (raw == null || raw.isEmpty) return [];
    try {
      final List<dynamic> decoded = jsonDecode(raw);
      return decoded.map((e) => Map<String, dynamic>.from(e)).toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> removeSyncedOperations(
    List<String> syncedOperationIds,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    final queue = await getPendingSyncQueue();
    final remaining = queue
        .where((op) => !syncedOperationIds.contains(op['operation_id']))
        .toList();
    await prefs.setString(_keySyncQueue, jsonEncode(remaining));
  }
}
