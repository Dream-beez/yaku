// SPDX-FileCopyrightText: 2024 grow platform GmbH
//
// SPDX-License-Identifier: MIT

export class Login {
  constructor(
    public userUuid: string,
    public userName: string,
    public email: string,
    public jwtToken: string,
    public refreshToken: string,
    public jwtTTL: number,
    public orgName: string,
    public orgUuid: string,
    public sessionStartTime: number,
  ) {}
}
