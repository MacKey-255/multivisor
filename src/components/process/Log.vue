<template>
  <v-bottom-sheet v-model="visible">
    <v-toolbar dense height="32px"
               :color="log.stream === 'err' ? 'orange' : 'blue'">
      <h5>{{ title }}</h5>
      <v-spacer></v-spacer>
      <v-tooltip bottom>
        <v-switch slot="activator" color="indigo"
                  style="height: 32px;"
                  v-model="autoScroll">
        </v-switch>
        <span>auto-scroll ({{ autoScroll ? 'On' : 'Off' }})</span>
      </v-tooltip>
      <v-tooltip bottom>
        <v-chip slot="activator" color="indigo white--text" dark>
          {{ localSizeStr }}
          <v-btn icon small left class="ml-0" @click="text=''">
            <v-icon>delete</v-icon>
          </v-btn>
        </v-chip>
      <span>Web console log size</span>
      </v-tooltip>
      <v-tooltip bottom>
        <v-chip slot="activator" class="indigo white--text" dark>
          {{ sizeStr }}
        </v-chip>
      <span>Remote log size</span>
      </v-tooltip>
      <v-btn icon dark small @click="maximize=!maximize" class="mr-2">
        <v-icon v-if="maximize">expand_more</v-icon>
        <v-icon v-else>expand_less</v-icon>
      </v-btn>
    </v-toolbar>
    <v-progress-linear :indeterminate="active" height=1 class="my-0"
                       background-color="white" color="deep-purple">
    </v-progress-linear>
    <v-card>
      <v-card-text :style="windowSize" id="logContent"
                   class="scroll-y black white--text px-2 py-0">
        <pre class="logConsole ">
          {{ text }}
        </pre>
      </v-card-text>
    </v-card>
  </v-bottom-sheet>
</template>

<script>
import * as multivisor from '../../multivisor'

export default {
  name: 'LogSheet',
  data () {
    return {
      text: '',
      size: 0,
      maximize: false,
      autoScroll: true,
      eventSource: null
    }
  },

  watch: {
    visible () { this.viewLog() }
  },

  computed: {
    log () {
      return this.$store.state.log
    },
    visible: {
      get () { return this.$store.state.log.visible },
      set (v) { this.$store.commit('setLogVisible', v) }
    },
    title () {
      if (!this.log.visible) {
        return ''
      }
      return `${this.log.stream === 'out' ? 'O-log of ' : 'E-log of '}
      ${this.log.process.name} on ${this.log.process.supervisor}`
    },
    windowSize () {
      let h = window.innerHeight
      return `height: ${this.maximize ? h - 80 : Math.min(h / 3, 300)}px;`
    },
    sizeStr () {
      return multivisor.formatBytes(this.size)
    },
    localSizeStr () {
      return multivisor.formatBytes(this.text.length)
    },
    active () {
      return this.eventSource && this.eventSource.readyState < 2
    }
  },

  methods: {
    appendLogMessage (data) {
      this.size = data.size
      if (data.message) {
        this.text += data.message
        /* At 10Mb, cut log to 9Mb */
        if (this.text.length > 1e7) {
          this.text = this.text.substr(-9000000)
        }
      }
      if (this.autoScroll) {
        let logTag = document.getElementById('logContent')
        setTimeout(() => { logTag.scrollTop = logTag.scrollHeight }, 100)
      }
    },
    viewLog () {
      let log = this.log
      if (this.eventSource !== null) {
        this.text = ''
        this.size = 0
        this.eventSource.close()
      }
      if (!log.visible) {
        return
      }
      let eventSource = new EventSource(`api/process/log/${log.stream}/tail/${log.process.uid}`)
      eventSource.onmessage = event => {
        let data = JSON.parse(event.data)
        this.appendLogMessage(data)
      }
      eventSource.onopen = event => {
        console.debug(log.stream + ' stream opened for ' + this.log.process.uid)
      }
      eventSource.onclose = event => {
        this.eventSource = null
      }
      eventSource.onerror = event => {
        this.eventSource.close()
        this.eventSource = null
      }
      this.eventSource = eventSource
    }
  }
}
</script>
<style scoped>
.logConsole {
  font-size: small;
}
</style>
