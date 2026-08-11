<script>
import { ref, computed } from 'vue';
import { useKeyboardEvents } from 'dashboard/composables/useKeyboardEvents';
import { useCaptain } from 'dashboard/composables/useCaptain';
import { useTrack } from 'dashboard/composables';
import { vOnClickOutside } from '@vueuse/components';
import { REPLY_EDITOR_MODES, CHAR_LENGTH_WARNING } from './constants';
import { CAPTAIN_EVENTS } from 'dashboard/helper/AnalyticsHelper/events';
import NextButton from 'dashboard/components-next/button/Button.vue';
import EditorModeToggle from './EditorModeToggle.vue';
import CopilotMenuBar from './CopilotMenuBar.vue';
import { useProtonConfig } from 'dashboard/composables/useProtonConfig';
import { callAssist } from 'dashboard/api/protonAssist';
import { useUISettings } from 'dashboard/composables/useUISettings';
import { useStore } from 'dashboard/composables/store';

// Actions intercepted by Proton backend when ai_assist feature is enabled
const PROTON_ACTIONS = {
  reply_suggestion: 'suggest',
  summarize: 'summarize',
  ask_copilot: 'ask',
};

// Where each action's result is inserted: the 'reply' box or a private 'note'.
const PROTON_ACTION_MODE = {
  reply_suggestion: 'reply',
  summarize: 'note',
  ask_copilot: 'reply',
};

export default {
  name: 'ReplyTopPanel',
  components: {
    NextButton,
    EditorModeToggle,
    CopilotMenuBar,
  },
  directives: {
    OnClickOutside: vOnClickOutside,
  },
  props: {
    mode: {
      type: String,
      default: REPLY_EDITOR_MODES.REPLY,
    },
    isReplyRestricted: {
      type: Boolean,
      default: false,
    },
    disabled: {
      type: Boolean,
      default: false,
    },
    isEditorDisabled: {
      type: Boolean,
      default: false,
    },
    conversationId: {
      type: Number,
      default: null,
    },
    isMessageLengthReachingThreshold: {
      type: Boolean,
      default: () => false,
    },
    charactersRemaining: {
      type: Number,
      default: () => 0,
    },
    editorContent: {
      type: String,
      default: undefined,
    },
    hasContent: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['setReplyMode', 'toggleEditorSize', 'executeCopilotAction', 'protonAssistResult'],
  setup(props, { emit }) {
    const setReplyMode = mode => {
      emit('setReplyMode', mode);
    };
    const handleReplyClick = () => {
      if (props.isReplyRestricted) return;
      setReplyMode(REPLY_EDITOR_MODES.REPLY);
    };
    const handleNoteClick = () => {
      setReplyMode(REPLY_EDITOR_MODES.NOTE);
    };
    const handleModeToggle = () => {
      const newMode =
        props.mode === REPLY_EDITOR_MODES.REPLY
          ? REPLY_EDITOR_MODES.NOTE
          : REPLY_EDITOR_MODES.REPLY;
      setReplyMode(newMode);
    };

    const { captainTasksEnabled } = useCaptain();
    const { hasFeature } = useProtonConfig();
    const { updateUISettings } = useUISettings();
    const store = useStore();
    const protonEnabled = computed(() => hasFeature('ai_assist'));
    const showAiButton = computed(
      () => captainTasksEnabled.value || protonEnabled.value
    );

    const showCopilotMenu = ref(false);
    const copilotToggleRef = ref(null);

    const handleCopilotAction = async (actionKey, data) => {
      showCopilotMenu.value = false;

      // Proton: Ask Copilot opens the multi-turn chat panel instead of
      // the one-shot /assist/ask, when the copilot feature is enabled.
      if (actionKey === 'ask_copilot' && hasFeature('copilot')) {
        updateUISettings({ is_proton_copilot_open: true });
        return;
      }

      if (protonEnabled.value && PROTON_ACTIONS[actionKey]) {
        const protonAction = PROTON_ACTIONS[actionKey];
        const chat = store.getters.getSelectedChat;
        // Structured, NOT pre-rendered: the backend owns transcript wording
        // and attachment markers from one registry. Keeping a label table
        // here too would be a second registry to drift. Note the second
        // filter keeps caption-less messages -- a voice note or photo sent
        // with no text used to be dropped here, so the AI was never told it
        // existed and asked the customer to explain "this one".
        const messages = (chat?.messages || [])
          .filter(m => [0, 1].includes(m.message_type))
          .filter(m => m.content || (m.attachments || []).length)
          .map(m => ({
            role: m.message_type === 0 ? 'customer' : 'agent',
            content: m.content || '',
            attachments: (m.attachments || []).map(a => ({
              file_type: a.file_type || 'file',
            })),
          }));
        const payload = {
          conversation_id: String(props.conversationId),
          messages,
        };
        if (actionKey === 'ask_copilot' && data) payload.question = data;
        try {
          const result = await callAssist(protonAction, payload);
          const text = result.draft || result.summary || result.answer || '';
          const mode = PROTON_ACTION_MODE[actionKey] || 'reply';
          if (text)
            emit('protonAssistResult', { text, mode, sources: result.sources || [] });
        } catch (err) {
          // eslint-disable-next-line no-console
          console.error('[proton-assist]', actionKey, 'failed', err);
        }
        return;
      }

      emit('executeCopilotAction', actionKey, data || props.editorContent);
    };

    const toggleCopilotMenu = () => {
      const isOpening = !showCopilotMenu.value;
      if (isOpening && captainTasksEnabled.value) {
        useTrack(CAPTAIN_EVENTS.EDITOR_AI_MENU_OPENED, {
          conversationId: props.conversationId,
          entryPoint: 'top_panel',
        });
      }
      showCopilotMenu.value = isOpening;
    };

    const handleClickOutside = () => {
      showCopilotMenu.value = false;
    };

    const keyboardEvents = {
      'Alt+KeyP': {
        action: () => handleNoteClick(),
        allowOnFocusedInput: false,
      },
      'Alt+KeyL': {
        action: () => handleReplyClick(),
        allowOnFocusedInput: false,
      },
    };
    useKeyboardEvents(keyboardEvents);

    return {
      handleModeToggle,
      handleReplyClick,
      handleNoteClick,
      REPLY_EDITOR_MODES,
      captainTasksEnabled,
      protonEnabled,
      showAiButton,
      handleCopilotAction,
      showCopilotMenu,
      copilotToggleRef,
      toggleCopilotMenu,
      handleClickOutside,
    };
  },
  computed: {
    replyButtonClass() {
      return {
        'is-active': this.mode === REPLY_EDITOR_MODES.REPLY,
      };
    },
    noteButtonClass() {
      return {
        'is-active': this.mode === REPLY_EDITOR_MODES.NOTE,
      };
    },
    charLengthClass() {
      return this.charactersRemaining < 0 ? 'text-n-ruby-9' : 'text-n-slate-11';
    },
    characterLengthWarning() {
      return this.charactersRemaining < 0
        ? `${-this.charactersRemaining} ${CHAR_LENGTH_WARNING.NEGATIVE}`
        : `${this.charactersRemaining} ${CHAR_LENGTH_WARNING.UNDER_50}`;
    },
  },
};
</script>

<template>
  <div
    class="flex justify-between gap-2 h-[3.25rem] items-center ltr:pl-3 ltr:pr-2 rtl:pr-3 rtl:pl-2"
  >
    <EditorModeToggle
      :mode="mode"
      :disabled="disabled"
      :is-reply-restricted="isReplyRestricted"
      @toggle-mode="handleModeToggle"
    />
    <div class="flex items-center mx-4 my-0">
      <div v-if="isMessageLengthReachingThreshold" class="text-xs">
        <span :class="charLengthClass">
          {{ characterLengthWarning }}
        </span>
      </div>
    </div>
    <div v-if="showAiButton" class="flex items-center gap-2">
      <div class="relative">
        <NextButton
          ref="copilotToggleRef"
          ghost
          :disabled="disabled || isEditorDisabled"
          :class="{
            'text-n-violet-9 hover:enabled:!bg-n-violet-3': !showCopilotMenu,
            'text-n-violet-9 bg-n-violet-3': showCopilotMenu,
          }"
          sm
          icon="i-ph-sparkle-fill"
          @click="toggleCopilotMenu"
        />
        <CopilotMenuBar
          v-if="showCopilotMenu"
          v-on-click-outside="[
            handleClickOutside,
            { ignore: [copilotToggleRef] },
          ]"
          :has-selection="false"
          :has-content="hasContent"
          :conversation-id="conversationId"
          class="ltr:right-0 rtl:left-0 bottom-full mb-2"
          @execute-copilot-action="handleCopilotAction"
        />
      </div>
      <NextButton
        ghost
        class="text-n-slate-11"
        sm
        icon="i-lucide-maximize-2"
        @click="$emit('toggleEditorSize')"
      />
    </div>
  </div>
</template>
