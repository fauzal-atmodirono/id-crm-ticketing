// Tracker tab: Campaigns. Creating or sending a campaign reaches real
// WhatsApp channels, which the suite never does: every row is manual.
import { manual, trackerModule } from '../lib/tracker';

trackerModule('Campaigns', {
  'CAMP-001': manual('creating a WhatsApp campaign targets real channels (send to test contacts only, by hand)'),
  'CAMP-002': manual('a scheduled send received on the test handset'),
  'CAMP-003': manual('a sent campaign’s sent and delivered counts'),
});
