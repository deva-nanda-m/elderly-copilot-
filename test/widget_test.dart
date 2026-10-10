import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:elderly_conv_copilot/main.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('ElderCare Copilot loads AuthScreen on first launch', (WidgetTester tester) async {
    await tester.pumpWidget(const ElderCareApp());
    await tester.pumpAndSettle();

    // Verifies that unauthenticated launch brings up senior-friendly authentication
    expect(find.byType(MaterialApp), findsOneWidget);
    expect(find.textContaining('ലോഗിൻ'), findsWidgets);
  });

  testWidgets('ElderCareHome loads when session is present', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ElderCareHome(
          session: const {
            'token': 'mock_token',
            'elder_id': 'elder_test',
            'name': 'മാധവൻ നായർ',
            'caregiver_linking_code': 'CARE-1234',
          },
          onLogout: () {},
        ),
      ),
    );
    await tester.pump();

    expect(find.text('മാധവൻ നായർ'), findsOneWidget);
    expect(find.textContaining('കെയർ കോഡ്: CARE-1234'), findsOneWidget);
  });
}