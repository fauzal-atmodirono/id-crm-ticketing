// Tracker tab: Custom features. Toggling a feature is forbidden on prod, and
// the access check needs a non-superadmin login, so every row is manual.
import { manual, trackerModule } from '../lib/tracker';

trackerModule('Custom features', {
  'FEAT-001': manual('a login as an admin who is not superadmin'),
  'FEAT-002': manual('turning a live feature off (forbidden in the automated run) and checking from a second user'),
  'FEAT-003': manual('turning the feature back on and checking from a second user'),
});
