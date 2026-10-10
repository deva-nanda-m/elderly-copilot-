import 'dart:convert';
import 'dart:io' show Platform;
import 'package:flutter/foundation.dart' show kIsWeb, debugPrint;
import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite/sqflite.dart' as sqflite;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Multiplatform local storage and SQLite repository for ElderCare Copilot.
/// Provides:
/// - Resilient SQLite persistence for all finalized user messages and Gemini responses
/// - Association with elder_id, session_id, message ID, role, and timestamp
/// - Pagination support for loading earlier conversation turns
/// - Duplicate-safe offline sync queue uploading to central database
/// - Secure session storage and elder account isolation
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

  // SQLite database singleton instance
  static sqflite.Database? _database;
  static bool _ffiInitialized = false;

  // ---------------------------------------------------------------------------
  // SQLITE DATABASE INITIALIZATION & ACCESS
  // ---------------------------------------------------------------------------

  /// Initializes the platform database factory (FFI for Desktop/Tests, native for Android/iOS)
  static void _ensureFfiInitialized() {
    if (_ffiInitialized) return;
    if (!kIsWeb) {
      if (Platform.isWindows || Platform.isLinux || Platform.isMacOS) {
        sqfliteFfiInit();
        sqflite.databaseFactory = databaseFactoryFfi;
      }
    }
    _ffiInitialized = true;
  }

  /// Returns the local SQLite database instance, creating tables and indexes if needed
  static Future<sqflite.Database> getDatabase({String? customPath}) async {
    if (_database != null && _database!.isOpen) {
      return _database!;
    }

    _ensureFfiInitialized();

    String dbPath;
    if (customPath != null) {
      dbPath = customPath;
    } else if (kIsWeb) {
      // In-memory or fallback path for web
      dbPath = 'eldercare_local.db';
    } else {
      final databasesDir = await sqflite.getDatabasesPath();
      dbPath = p.join(databasesDir, 'eldercare_local.db');
    }

    _database = await sqflite.openDatabase(
      dbPath,
      version: 1,
      onCreate: (db, version) async {
        // Chat messages table matching the central DB schema
        await db.execute('''
          CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            elder_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            audio_url TEXT,
            timestamp TEXT NOT NULL,
            metadata_json TEXT,
            sync_status TEXT NOT NULL DEFAULT 'pending'
          );
        ''');

        await db.execute('''
          CREATE INDEX IF NOT EXISTS idx_chat_messages_elder_time 
          ON chat_messages(elder_id, timestamp);
        ''');

        await db.execute('''
          CREATE INDEX IF NOT EXISTS idx_chat_messages_session 
          ON chat_messages(elder_id, session_id);
        ''');

        // Sync queue table for resilient offline synchronization
        await db.execute('''
          CREATE TABLE IF NOT EXISTS sync_queue (
            operation_id TEXT PRIMARY KEY,
            elder_id TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            data_json TEXT NOT NULL,
            client_timestamp TEXT NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending'
          );
        ''');

        await db.execute('''
          CREATE INDEX IF NOT EXISTS idx_sync_queue_elder_status 
          ON sync_queue(elder_id, status);
        ''');
      },
    );

    return _database!;
  }

  /// Closes database instance (useful during tests or logout)
  static Future<void> closeDatabase() async {
    if (_database != null && _database!.isOpen) {
      await _database!.close();
      _database = null;
    }
  }

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
    } catch (_) {}
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

  /// Clears active session on logout
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
  // SQLITE CHAT PERSISTENCE (STEP 3)
  // ---------------------------------------------------------------------------

  /// Saves a finalized user message or Gemini response to local SQLite.
  /// Automatically queues unsynced user messages for central DB synchronization.
  static Future<void> saveChatMessage({
    required String id,
    required String elderId,
    required String sessionId,
    required String role,
    required String text,
    String? audioUrl,
    required DateTime timestamp,
    String syncStatus = 'pending',
    Map<String, dynamic>? metadata,
  }) async {
    if (kIsWeb) {
      await _fallbackSaveMessage(
        id: id,
        elderId: elderId,
        sessionId: sessionId,
        role: role,
        text: text,
        audioUrl: audioUrl,
        timestamp: timestamp,
        syncStatus: syncStatus,
      );
      return;
    }

    try {
      final db = await getDatabase();
      final isoTimestamp = timestamp.toUtc().toIso8601String();
      final metaJson = jsonEncode(metadata ?? {});

      await db.insert(
        'chat_messages',
        {
          'id': id,
          'elder_id': elderId,
          'session_id': sessionId,
          'role': role,
          'text': text,
          'audio_url': audioUrl,
          'timestamp': isoTimestamp,
          'metadata_json': metaJson,
          'sync_status': syncStatus,
        },
        conflictAlgorithm: sqflite.ConflictAlgorithm.replace,
      );

      // If message is pending sync, queue operation idempotently
      if (syncStatus == 'pending') {
        final opId = 'op_$id';
        await db.insert(
          'sync_queue',
          {
            'operation_id': opId,
            'elder_id': elderId,
            'action': 'insert_message',
            'entity_id': id,
            'data_json': jsonEncode({
              'session_id': sessionId,
              'role': role,
              'text': text,
              'audio_url': audioUrl,
              'metadata': metadata ?? {},
            }),
            'client_timestamp': isoTimestamp,
            'retry_count': 0,
            'status': 'pending',
          },
          conflictAlgorithm: sqflite.ConflictAlgorithm.replace,
        );
      }
    } catch (e) {
      // Robust fallback to SharedPreferences if SQLite fails or on web
      debugPrint('SQLite saveChatMessage fallback: $e');
      await _fallbackSaveMessage(
        id: id,
        elderId: elderId,
        sessionId: sessionId,
        role: role,
        text: text,
        audioUrl: audioUrl,
        timestamp: timestamp,
        syncStatus: syncStatus,
      );
    }
  }

  /// Marks a specific message as synced in SQLite and removes from the sync queue
  static Future<void> markMessageSynced(String messageId) async {
    if (kIsWeb) {
      await _fallbackMarkSynced(messageId);
      return;
    }

    try {
      final db = await getDatabase();
      await db.update(
        'chat_messages',
        {'sync_status': 'synced'},
        where: 'id = ?',
        whereArgs: [messageId],
      );
      await db.delete(
        'sync_queue',
        where: 'entity_id = ? OR operation_id = ?',
        whereArgs: [messageId, 'op_$messageId'],
      );
    } catch (e) {
      debugPrint('SQLite markMessageSynced fallback: $e');
    }
  }

  /// Restores chat history for an authenticated elder from SQLite.
  /// Strictly isolates records by elderId and orders chronologically (oldest to newest).
  static Future<List<Map<String, dynamic>>> getChatHistory({
    required String elderId,
    String? sessionId,
    int limit = 30,
    int offset = 0,
  }) async {
    if (kIsWeb) {
      return _fallbackGetHistory(elderId);
    }

    try {
      final db = await getDatabase();
      String whereClause = 'elder_id = ?';
      List<dynamic> whereArgs = [elderId];

      if (sessionId != null) {
        whereClause += ' AND session_id = ?';
        whereArgs.add(sessionId);
      }

      final rows = await db.query(
        'chat_messages',
        where: whereClause,
        whereArgs: whereArgs,
        orderBy: 'timestamp DESC',
        limit: limit,
        offset: offset,
      );

      // Return in chronological order for UI display
      final results = rows.map((r) => Map<String, dynamic>.from(r)).toList();
      return results.reversed.toList();
    } catch (e) {
      debugPrint('SQLite getChatHistory fallback: $e');
      return _fallbackGetHistory(elderId);
    }
  }

  /// Loads earlier chat messages before a given timestamp for lazy loading / pagination
  static Future<List<Map<String, dynamic>>> getEarlierMessages({
    required String elderId,
    String? sessionId,
    required DateTime beforeTimestamp,
    int limit = 20,
  }) async {
    try {
      final db = await getDatabase();
      final beforeIso = beforeTimestamp.toUtc().toIso8601String();

      String whereClause = 'elder_id = ? AND timestamp < ?';
      List<dynamic> whereArgs = [elderId, beforeIso];

      if (sessionId != null) {
        whereClause += ' AND session_id = ?';
        whereArgs.add(sessionId);
      }

      final rows = await db.query(
        'chat_messages',
        where: whereClause,
        whereArgs: whereArgs,
        orderBy: 'timestamp DESC',
        limit: limit,
      );

      final results = rows.map((r) => Map<String, dynamic>.from(r)).toList();
      return results.reversed.toList();
    } catch (e) {
      debugPrint('SQLite getEarlierMessages fallback: $e');
      return [];
    }
  }

  /// Returns conversational context turns strictly for the current session and elder.
  /// Never mixes sessions or different elders.
  static Future<List<Map<String, String>>> getContextTurns({
    required String elderId,
    required String sessionId,
    int maxTurns = 20,
  }) async {
    try {
      final db = await getDatabase();
      final rows = await db.query(
        'chat_messages',
        where: 'elder_id = ? AND session_id = ?',
        whereArgs: [elderId, sessionId],
        orderBy: 'timestamp DESC',
        limit: maxTurns,
      );

      final reversedRows = rows.reversed.toList();
      return reversedRows.map((r) {
        final role = r['role'] == 'user' ? 'user' : 'model';
        return {
          'role': role,
          'text': (r['text'] ?? '').toString(),
        };
      }).toList();
    } catch (_) {
      return [];
    }
  }

  /// Clears chat history for an elder (e.g. on account deletion or local reset)
  static Future<void> clearElderChat(String elderId) async {
    try {
      final db = await getDatabase();
      await db.delete('chat_messages', where: 'elder_id = ?', whereArgs: [elderId]);
      await db.delete('sync_queue', where: 'elder_id = ?', whereArgs: [elderId]);
    } catch (_) {}

    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove('$_keyOfflineMessagesPrefix$elderId');
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // DUPLICATE-SAFE SYNCHRONIZATION QUEUE (STEP 3)
  // ---------------------------------------------------------------------------

  /// Fetches pending synchronization operations for an elder
  static Future<List<Map<String, dynamic>>> getPendingSyncOperations(
    String elderId,
  ) async {
    try {
      final db = await getDatabase();
      final rows = await db.query(
        'sync_queue',
        where: 'elder_id = ? AND status = ?',
        whereArgs: [elderId, 'pending'],
        orderBy: 'client_timestamp ASC',
      );

      return rows.map((r) {
        final map = Map<String, dynamic>.from(r);
        try {
          map['data'] = jsonDecode(r['data_json'] as String);
        } catch (_) {
          map['data'] = {};
        }
        return map;
      }).toList();
    } catch (_) {
      return getPendingSyncQueue();
    }
  }

  /// Idempotently synchronizes pending offline operations with central FastAPI backend
  static Future<Map<String, dynamic>> syncPendingQueue({
    required String elderId,
    required String token,
    required String baseUrl,
    http.Client? httpClient,
  }) async {
    final client = httpClient ?? http.Client();
    try {
      final pendingOps = await getPendingSyncOperations(elderId);
      if (pendingOps.isEmpty) {
        return {'status': 'noop', 'synced_count': 0};
      }

      // Format payload according to SyncBatchRequest schema
      final batchPayload = {
        'elder_id': elderId,
        'operations': pendingOps.map((op) => {
          'operation_id': op['operation_id'],
          'elder_id': elderId,
          'action': op['action'],
          'entity_id': op['entity_id'],
          'data': op['data'] ?? {},
          'client_timestamp': op['client_timestamp'],
          'status': 'pending',
        }).toList(),
      };

      final response = await client.post(
        Uri.parse('$baseUrl/api/sync'),
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer $token',
        },
        body: jsonEncode(batchPayload),
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(utf8.decode(response.bodyBytes));
        final List<dynamic> syncedIds = data['synced_operations'] ?? [];

        // Update local database to reflect synced state
        final db = await getDatabase();
        for (final opId in syncedIds) {
          final idStr = opId.toString();
          // Remove operation from queue
          await db.delete('sync_queue', where: 'operation_id = ?', whereArgs: [idStr]);
          // Mark associated chat message as synced
          final entityId = idStr.startsWith('op_') ? idStr.substring(3) : idStr;
          await db.update(
            'chat_messages',
            {'sync_status': 'synced'},
            where: 'id = ?',
            whereArgs: [entityId],
          );
        }

        // Also clean up SharedPreferences queue if applicable
        try {
          await removeSyncedOperations(syncedIds.map((e) => e.toString()).toList());
        } catch (_) {}

        return {
          'status': 'success',
          'synced_count': syncedIds.length,
          'synced_ids': syncedIds,
        };
      } else {
        return {
          'status': 'error',
          'status_code': response.statusCode,
          'message': 'Server rejected sync request',
        };
      }
    } catch (e) {
      // Safe offline catch: no crash, operations remain queued for next retry
      return {
        'status': 'network_offline',
        'error': e.toString(),
      };
    } finally {
      if (httpClient == null) {
        client.close();
      }
    }
  }

  // ---------------------------------------------------------------------------
  // FALLBACK IMPLEMENTATIONS (WEB / SHARED PREFERENCES)
  // ---------------------------------------------------------------------------

  static Future<void> _fallbackSaveMessage({
    required String id,
    required String elderId,
    required String sessionId,
    required String role,
    required String text,
    String? audioUrl,
    required DateTime timestamp,
    String syncStatus = 'pending',
  }) async {
    final current = await _fallbackGetHistory(elderId);
    current.add({
      'id': id,
      'elder_id': elderId,
      'session_id': sessionId,
      'role': role,
      'text': text,
      'audio_url': audioUrl,
      'timestamp': timestamp.toUtc().toIso8601String(),
      'sync_status': syncStatus,
    });
    final trimmed = current.length > 80 ? current.sublist(current.length - 80) : current;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('$_keyOfflineMessagesPrefix$elderId', jsonEncode(trimmed));

    if (syncStatus == 'pending') {
      await queueSyncOperation({
        'operation_id': 'op_$id',
        'elder_id': elderId,
        'action': 'insert_message',
        'entity_id': id,
        'data': {
          'session_id': sessionId,
          'role': role,
          'text': text,
          'audio_url': audioUrl,
        },
        'client_timestamp': timestamp.toUtc().toIso8601String(),
      });
    }
  }

  static Future<List<Map<String, dynamic>>> _fallbackGetHistory(String elderId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString('$_keyOfflineMessagesPrefix$elderId');
      if (raw == null || raw.isEmpty) return [];
      final List<dynamic> decoded = jsonDecode(raw);
      return decoded.map((e) => Map<String, dynamic>.from(e)).toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> _fallbackMarkSynced(String messageId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final keys = prefs.getKeys().where((k) => k.startsWith(_keyOfflineMessagesPrefix));
      for (final k in keys) {
        final raw = prefs.getString(k);
        if (raw != null && raw.isNotEmpty) {
          final List<dynamic> list = jsonDecode(raw);
          bool modified = false;
          for (final item in list) {
            if (item['id'] == messageId) {
              item['sync_status'] = 'synced';
              modified = true;
            }
          }
          if (modified) {
            await prefs.setString(k, jsonEncode(list));
          }
        }
      }
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // BACKWARDS COMPATIBILITY & MEDICINES CACHE
  // ---------------------------------------------------------------------------

  static Future<void> saveLocalChatHistory(
    String elderId,
    List<Map<String, dynamic>> messages,
  ) async {
    for (final m in messages) {
      await saveChatMessage(
        id: m['id'] ?? 'msg_${elderId}_${DateTime.now().millisecondsSinceEpoch}',
        elderId: elderId,
        sessionId: m['session_id'] ?? 'default_session',
        role: (m['is_user'] == true || m['role'] == 'user') ? 'user' : 'assistant',
        text: m['text'] ?? '',
        audioUrl: m['audio_url'],
        timestamp: m['timestamp'] != null
            ? DateTime.tryParse(m['timestamp']) ?? DateTime.now()
            : DateTime.now(),
        syncStatus: m['sync_status'] ?? 'synced',
      );
    }
  }

  static Future<List<Map<String, dynamic>>> getLocalChatHistory(
    String elderId,
  ) async {
    return getChatHistory(elderId: elderId, limit: 60);
  }

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
