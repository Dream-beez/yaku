// SPDX-FileCopyrightText: 2024 grow platform GmbH
//
// SPDX-License-Identifier: MIT

import yp from '../yaku-prompts.js'

import { loginOAuth } from '../oauth.js'
import {
  createEnvironment,
  deleteEnvironment,
  Environment,
  loadEnvironments,
  selectEnvironment,
  updateEnvironmentByKey,
} from './environment.js'
import { Namespace } from '@B-S-F/yaku-client-lib'
import {
  consoleErrorRed,
  handleRestApiError,
  urlToApiUrl,
  validateUrl,
} from '../common.js'
import { selectNamespace } from './namespaces.js'
import { loginToken } from '../token-auth.js'
import { connect } from '../connect.js'

export async function login(
  envName: string,
  options: {
    url: string
    namespace: number
    web: boolean
    token: string | boolean
    admin: boolean
  },
) {
  // get env name
  try {
    envName = await getEnvironment(envName)
  } catch (err) {
    if (err instanceof Error) {
      consoleErrorRed(err.message)
    } else {
      consoleErrorRed(
        'An unknown error occurred while getting the environment name.',
      )
    }
    return
  }
  // get url
  try {
    const url = validateUrl(await getUrl(options.url, envName))
    const apiUrl = urlToApiUrl(url)
    options.url = url

    if (url !== apiUrl) {
      const shouldUpdateUrl = await yp.confirm(
        `The specified ${url} is not a valid API url. Do you want to replace it with ${apiUrl}?`,
      )

      if (shouldUpdateUrl) {
        options.url = apiUrl
      }
    }
  } catch (err) {
    if (err instanceof Error) {
      consoleErrorRed(err.message)
    } else {
      consoleErrorRed(
        'An unknown error occurred while getting the environment URL.',
      )
    }
    return
  }
  // get login method
  let loginMethod: string
  try {
    loginMethod = await getLoginMethod(options)
  } catch (err) {
    if (err instanceof Error) {
      consoleErrorRed(err.message)
    } else {
      consoleErrorRed(
        'An unknown error occurred while getting the login method.',
      )
    }
    return
  }
  // login and create environment
  try {
    await loginAndCreateEnv(loginMethod, envName, options.url, options.token)
    console.log(
      `Login information have been saved to environment '${envName}'.`,
    )
    console.log(`Environment '${envName}' is now activated.`)
  } catch (err) {
    if (err instanceof Error) {
      consoleErrorRed(err.message)
    } else {
      consoleErrorRed('An unknown error occurred while logging in.')
    }
    return
  }
  // select namespace
  try {
    await selectNamespaceAndUpdateEnv(envName, options)
  } catch (err) {
    if (err instanceof Error) {
      consoleErrorRed(err.message)
    } else {
      consoleErrorRed(
        'An unknown error occurred while selecting the namespace.',
      )
    }
    return
  }
}

export async function getEnvironment(envName: string): Promise<string> {
  if (envName) {
    return envName
  }
  const envs = loadEnvironments()
  const shouldCreate = await yp.confirm(
    'Do you want to create a new environment?',
  )
  if (!shouldCreate) {
    if (envs.length > 0) {
      return await selectEnvironment(envs)
    }
    throw new Error(
      `No environments available for selection, please create one first!`,
    )
  }

  const newEnvName = await yp.input('Name of the environment')
  if (envs.find((env) => env.name === newEnvName)) {
    throw new Error(`Environment with name '${newEnvName}' already exists!`)
  }
  return newEnvName
}

export async function getUrl(url: string, envName: string): Promise<string> {
  if (!url) {
    url = await yp.input(
      'URL of the environment',
      loadEnvironments().find((env) => env.name === envName)?.url,
    )
  }
  return url
}

export async function getLoginMethod(options: {
  web: boolean
  admin: boolean
  token: string | boolean
}): Promise<string> {
  let loginMethod: string
  if (options.admin) {
    loginMethod = 'oauth-admin'
  } else if (options.web) {
    loginMethod = 'oauth'
  } else if (options.token && typeof options.token === 'string') {
    loginMethod = 'token'
  } else if (options.token && typeof options.token !== 'string') {
    loginMethod = 'token-prompt'
  } else {
    loginMethod = await yp.select(
      'How would you like to authenticate Yaku CLI?',
      [
        {
          name: 'Login with web browser',
          value: 'oauth',
        },
        {
          name: 'token-prompt',
          value: 'Login with an authentication token',
        },
      ],
    )
  }
  return loginMethod
}

export async function loginAndCreateEnv(
  loginMethod: string,
  envName: string,
  url: string,
  token: string | boolean,
) {
  let env: Environment
  if (loginMethod === 'oauth') {
    try {
      env = await loginOAuth(envName, url)
    } catch (err) {
      const msg = 'OAuth login failed! Please try again.'
      if (err instanceof Error) {
        throw new Error(`${msg}\nError was: ${err.message}`)
      }
    }
  } else if (loginMethod === 'oauth-admin') {
    try {
      env = await loginOAuth(envName, url, ['global'])
    } catch (err) {
      const msg = 'OAuth admin login failed! Please try again.'
      if (err instanceof Error) {
        throw new Error(`${msg}\nError was: ${err.message}`)
      }
    }
  } else {
    if (loginMethod === 'token-prompt') {
      token = await yp.input('Paste your authentication token')
    }
    try {
      env = await loginToken(token as string, envName, url)
      createEnvironment(env!)
      const client = (await connect()).client
      await client.listNewTokens() // verify if the token is valid
      return
    } catch (err) {
      await deleteEnvironment(envName, true)
      const msg = 'Token login failed! Please try again.'
      if (err instanceof Error) {
        throw new Error(`${msg}\nError was: ${err.message}`)
      }
    }
  }
  createEnvironment(env!)
}

export async function selectNamespaceAndUpdateEnv(
  envName: string,
  options: { namespace: number },
) {
  let namespaces: Namespace[] = []
  const client = (await connect()).client
  let namespaceId: string | number | undefined
  try {
    namespaces = await client.getNamespaces()
  } catch (err) {
    consoleErrorRed('Failed to get namespaces!')
    handleRestApiError(err)
  }
  if (!options.namespace) {
    namespaceId = await selectNamespace(namespaces)
    updateEnvironmentByKey(envName, 'namespace', namespaceId)
  } else {
    namespaceId = Number(options.namespace)
    if (!namespaces.find((ns) => ns.id === namespaceId)) {
      throw new Error('Namespace does not exist!')
    }
    updateEnvironmentByKey(envName, 'namespace', options.namespace.toString())
  }
}
