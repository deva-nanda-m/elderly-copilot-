import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:elderly_conv_copilot/services/local_storage_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late String testDbPath;

  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    final tempDir = await Directory.systemTemp.createTemp('eldercare_test_');
    testDbPath = '${tempDir.path}${Platform.pathSeparator}test_eldercare.db';
    await LocalStorageService.closeDatabase();
    await LocalStorageService.getDatabase(customPath: testDbPath);
  });

  tearDown(() async {
    await LocalStorageService.closeDatabase();
    try {
      final file = File(testDbPath);
      if (await file.exists()) {
        await file.delete();
      }
    } catch (_) {}
  });

  group('Step 3: SQLite Chat Persistence Tests', () {
    test('1. Save finalized user message & Gemini response to SQLite with all associations', () async {
      final now = DateTime.now().toUtc();
      const elderId = 'elder_madhavan_101';
      const sessionId = 'session_20261010_01';
      const userMsgId = 'msg_user_001';
      const botMsgId = 'msg_bot_001';

      // 1. User message
      await LocalStorageService.saveChatMessage(
        id: userMsgId,
        elderId: elderId,
        sessionId: sessionId,
        role: 'user',
        text: 'സുഖമാണോ കുഞ്ചൂ?',
        timestamp: now,
        syncStatus: 'pending',
      );

      // 2. Gemini response
      await LocalStorageService.saveChatMessage(
        id: botMsgId,
        elderId: elderId,
        sessionId: sessionId,
        role: 'assistant',
        text: 'എനിക്ക് നല്ല സുഖമാണ് അപ്പൂപ്പാ! അപ്പൂപ്പന് സുഖമല്ലേ?',
        audioUrl: '/api/tts/stream/audio123',
        timestamp: now.add(const Duration(seconds: 2)),
        syncStatus: 'synced',
      );

      // Verify records in local SQLite
      final history = await LocalStorageService.getChatHistory(elderId: elderId);
      expect(history.length, equals(2));

      final first = history[0];
      expect(first['id'], equals(userMsgId));
      expect(first['elder_id'], equals(elderId));
      expect(first['session_id'], equals(sessionId));
      expect(first['role'], equals('user'));
      expect(first['text'], equals('സുഖമാണോ കുഞ്ചൂ?'));
      expect(first['sync_status'], equals('pending'));

      final second = history[1];
      expect(second['id'], equals(botMsgId));
      expect(second['elder_id'], equals(elderId));
      expect(second['session_id'], equals(sessionId));
      expect(second['role'], equals('assistant'));
      expect(second['text'], contains('നല്ല സുഖമാണ്'));
      expect(second['audio_url'], equals('/api/tts/stream/audio123'));
      expect(second['sync_status'], equals('synced'));
    });

    test('2. Save user message before network requests so temporary failures do not lose it', () async {
      const elderId = 'elder_madhavan_101';
      const sessionId = 'session_offline_test';
      const userMsgId = 'msg_offline_001';
      final timestamp = DateTime.now().toUtc();

      // Pre-network save
      await LocalStorageService.saveChatMessage(
        id: userMsgId,
        elderId: elderId,
        sessionId: sessionId,
        role: 'user',
        text: 'എന്റെ മരുന്ന് കഴിക്കാൻ സമയമായോ?',
        timestamp: timestamp,
        syncStatus: 'pending',
      );

      // Simulate network failure by doing nothing (no response received)
      // Verify message is safely in SQLite
      final history = await LocalStorageService.getChatHistory(elderId: elderId);
      expect(history.length, equals(1));
      expect(history.first['id'], equals(userMsgId));
      expect(history.first['text'], equals('എന്റെ മരുന്ന് കഴിക്കാൻ സമയമായോ?'));
      expect(history.first['sync_status'], equals('pending'));

      // Verify it was added to sync_queue
      final pendingOps = await LocalStorageService.getPendingSyncOperations(elderId);
      expect(pendingOps.length, equals(1));
      expect(pendingOps.first['entity_id'], equals(userMsgId));
      expect(pendingOps.first['action'], equals('insert_message'));
    });

    test('3. Restore chat history across simulated app restarts', () async {
      const elderId = 'elder_reopen_test';
      const sessionId = 'session_persist';

      for (int i = 1; i <= 5; i++) {
        await LocalStorageService.saveChatMessage(
          id: 'msg_persist_$i',
          elderId: elderId,
          sessionId: sessionId,
          role: i.isOdd ? 'user' : 'assistant',
          text: 'സന്ദേശം $i',
          timestamp: DateTime.now().toUtc().add(Duration(seconds: i)),
          syncStatus: 'synced',
        );
      }

      // Simulate app closure
      await LocalStorageService.closeDatabase();

      // Simulate app reopen
      await LocalStorageService.getDatabase(customPath: testDbPath);
      final restored = await LocalStorageService.getChatHistory(elderId: elderId);

      expect(restored.length, equals(5));
      expect(restored.first['text'], equals('സന്ദേശം 1'));
      expect(restored.last['text'], equals('സന്ദേശം 5'));
    });

    test('4. Maintain conversation context per session without mixing sessions', () async {
      const elderId = 'elder_context_test';
      const session1 = 'session_morning';
      const session2 = 'session_evening';

      // Morning session
      await LocalStorageService.saveChatMessage(
        id: 'msg_m1',
        elderId: elderId,
        sessionId: session1,
        role: 'user',
        text: 'രാവിലെ പ്രഭാതഭക്ഷണം കഴിച്ചു',
        timestamp: DateTime.now().toUtc().subtract(const Duration(hours: 5)),
      );
      await LocalStorageService.saveChatMessage(
        id: 'msg_m2',
        elderId: elderId,
        sessionId: session1,
        role: 'assistant',
        text: 'നല്ല കാര്യം അപ്പൂപ്പാ!',
        timestamp: DateTime.now().toUtc().subtract(const Duration(hours: 5, minutes: -1)),
      );

      // Evening session
      await LocalStorageService.saveChatMessage(
        id: 'msg_e1',
        elderId: elderId,
        sessionId: session2,
        role: 'user',
        text: 'വൈകുന്നേരത്തെ കാപ്പി കുടിച്ചു',
        timestamp: DateTime.now().toUtc(),
      );

      // Get context strictly for session2
      final contextSession2 = await LocalStorageService.getContextTurns(
        elderId: elderId,
        sessionId: session2,
      );

      expect(contextSession2.length, equals(1));
      expect(contextSession2.first['text'], equals('വൈകുന്നേരത്തെ കാപ്പി കുടിച്ചു'));
      expect(contextSession2.any((t) => t['text']!.contains('പ്രഭാതഭക്ഷണം')), isFalse);

      // Get context for session1
      final contextSession1 = await LocalStorageService.getContextTurns(
        elderId: elderId,
        sessionId: session1,
      );
      expect(contextSession1.length, equals(2));
      expect(contextSession1.any((t) => t['text']!.contains('വൈകുന്നേരം')), isFalse);
    });

    test('5. Strict data isolation between two elder accounts', () async {
      const elderA = 'elder_madhavan_111';
      const elderB = 'elder_saradha_222';

      // Elder A messages
      await LocalStorageService.saveChatMessage(
        id: 'msg_a1',
        elderId: elderA,
        sessionId: 'session_a',
        role: 'user',
        text: 'മാധവന്റെ രഹസ്യ സന്ദേശം',
        timestamp: DateTime.now().toUtc(),
      );

      // Elder B messages
      await LocalStorageService.saveChatMessage(
        id: 'msg_b1',
        elderId: elderB,
        sessionId: 'session_b',
        role: 'user',
        text: 'ശാരദയുടെ ചോദ്യം',
        timestamp: DateTime.now().toUtc(),
      );

      // Verify Elder A only sees their own messages
      final historyA = await LocalStorageService.getChatHistory(elderId: elderA);
      expect(historyA.length, equals(1));
      expect(historyA.first['text'], equals('മാധവന്റെ രഹസ്യ സന്ദേശം'));
      expect(historyA.any((m) => m['elder_id'] == elderB), isFalse);

      // Verify Elder B only sees their own messages
      final historyB = await LocalStorageService.getChatHistory(elderId: elderB);
      expect(historyB.length, equals(1));
      expect(historyB.first['text'], equals('ശാരദയുടെ ചോദ്യം'));
      expect(historyB.any((m) => m['elder_id'] == elderA), isFalse);
    });

    test('6. Pagination: loading earlier messages support', () async {
      const elderId = 'elder_pagination_test';
      final baseTime = DateTime.utc(2026, 10, 10, 10, 0, 0);

      // Insert 25 messages chronologically
      for (int i = 1; i <= 25; i++) {
        await LocalStorageService.saveChatMessage(
          id: 'msg_page_$i',
          elderId: elderId,
          sessionId: 'session_p',
          role: i.isOdd ? 'user' : 'assistant',
          text: 'Turn $i',
          timestamp: baseTime.add(Duration(minutes: i)),
        );
      }

      // Initial load: latest 10 messages (turns 16 to 25)
      final initialLoad = await LocalStorageService.getChatHistory(
        elderId: elderId,
        limit: 10,
      );
      expect(initialLoad.length, equals(10));
      expect(initialLoad.first['text'], equals('Turn 16'));
      expect(initialLoad.last['text'], equals('Turn 25'));

      // Load earlier messages before Turn 16
      final oldestTimestamp = DateTime.parse(initialLoad.first['timestamp']);
      final earlier = await LocalStorageService.getEarlierMessages(
        elderId: elderId,
        beforeTimestamp: oldestTimestamp,
        limit: 10,
      );

      expect(earlier.length, equals(10));
      expect(earlier.first['text'], equals('Turn 6'));
      expect(earlier.last['text'], equals('Turn 15'));
    });

    test('7. Duplicate-safe synchronization queue and idempotency', () async {
      const elderId = 'elder_sync_test';
      const userMsgId = 'msg_sync_001';

      await LocalStorageService.saveChatMessage(
        id: userMsgId,
        elderId: elderId,
        sessionId: 'session_s',
        role: 'user',
        text: 'ഓഫ്‌ലൈൻ ചോദ്യം',
        timestamp: DateTime.now().toUtc(),
        syncStatus: 'pending',
      );

      final pending = await LocalStorageService.getPendingSyncOperations(elderId);
      expect(pending.length, equals(1));
      expect(pending.first['operation_id'], equals('op_$userMsgId'));

      // Mock central server response
      final mockClient = MockClient((request) async {
        expect(request.url.path, equals('/api/sync'));
        expect(
          request.headers['authorization'] ?? request.headers['Authorization'],
          equals('Bearer test_token'),
        );

        final body = jsonDecode(request.body);
        expect(body['elder_id'], equals(elderId));

        return http.Response(
          jsonEncode({
            'elder_id': elderId,
            'processed_count': 1,
            'synced_operations': ['op_$userMsgId'],
            'rejected_operations': [],
            'server_time': DateTime.now().toUtc().toIso8601String(),
          }),
          200,
        );
      });

      final result = await LocalStorageService.syncPendingQueue(
        elderId: elderId,
        token: 'test_token',
        baseUrl: 'http://127.0.0.1:8000',
        httpClient: mockClient,
      );

      expect(result['status'], equals('success'));
      expect(result['synced_count'], equals(1));

      // Verify message is now marked synced in SQLite
      final history = await LocalStorageService.getChatHistory(elderId: elderId);
      expect(history.first['sync_status'], equals('synced'));

      // Verify sync queue is now empty
      final remaining = await LocalStorageService.getPendingSyncOperations(elderId);
      expect(remaining.isEmpty, isTrue);

      // Re-running sync when empty should be a safe no-op
      final noopResult = await LocalStorageService.syncPendingQueue(
        elderId: elderId,
        token: 'test_token',
        baseUrl: 'http://127.0.0.1:8000',
        httpClient: mockClient,
      );
      expect(noopResult['status'], equals('noop'));
    });
  });
}
